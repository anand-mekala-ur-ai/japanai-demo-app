import os
import json
from typing import Dict, Any, List, Optional, Union, AsyncGenerator
from contextlib import asynccontextmanager
import uvicorn

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from anthropic import AsyncAnthropic

from assistant_stream.serialization import DataStreamResponse
from assistant_stream import create_run

from config import settings
from tools import TOOLS, execute_tool


# Helper functions to convert Anthropic messages to LangChain format
def create_human_message(text: str) -> dict:
    """Create a LangChain human message."""
    return {"type": "human", "content": text}


def create_ai_message(text: str = "", tool_calls: list = None) -> dict:
    """Create a LangChain AI message."""
    msg = {"type": "ai", "content": text}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def create_tool_message(tool_call_id: str, content: str) -> dict:
    """Create a LangChain tool message."""
    return {"type": "tool", "content": content, "tool_call_id": tool_call_id}


def convert_langchain_to_anthropic(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Convert LangChain-format messages to Anthropic API format.

    LangChain format:
    - {"type": "human", "content": "..."}
    - {"type": "ai", "content": "...", "tool_calls": [...]}
    - {"type": "tool", "content": "...", "tool_call_id": "..."}

    Anthropic format:
    - {"role": "user", "content": "..."}
    - {"role": "assistant", "content": [...]}
    - tool results are grouped into user messages
    """
    anthropic_messages = []
    i = 0

    while i < len(messages):
        msg = messages[i]
        msg_type = msg.get("type")

        if msg_type == "human":
            anthropic_messages.append({
                "role": "user",
                "content": msg.get("content", "")
            })
            i += 1

        elif msg_type == "ai":
            content = []
            text_content = msg.get("content", "")
            if text_content:
                content.append({"type": "text", "text": text_content})

            tool_calls = msg.get("tool_calls", [])
            for tc in tool_calls:
                content.append({
                    "type": "tool_use",
                    "id": tc.get("id"),
                    "name": tc.get("name"),
                    "input": tc.get("args", {})
                })

            if content:
                anthropic_messages.append({
                    "role": "assistant",
                    "content": content if len(content) > 1 or tool_calls else text_content
                })
            i += 1

        elif msg_type == "tool":
            # Collect consecutive tool messages into a single user message
            tool_results = []
            while i < len(messages) and messages[i].get("type") == "tool":
                tool_msg = messages[i]
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_msg.get("tool_call_id"),
                    "content": tool_msg.get("content", "")
                })
                i += 1

            anthropic_messages.append({
                "role": "user",
                "content": tool_results
            })
        else:
            # Skip unknown message types
            i += 1

    return anthropic_messages


class MessagePart(BaseModel):
    """A part of a user message."""
    type: str = Field(..., description="The type of message part")
    text: Optional[str] = Field(None, description="Text content")
    image: Optional[str] = Field(None, description="Image URL or data")


class UserMessage(BaseModel):
    """A user message."""
    role: str = Field(default="user", description="Message role")
    parts: List[MessagePart] = Field(..., description="Message parts")


class AddMessageCommand(BaseModel):
    """Command to add a new message to the conversation."""
    type: str = Field(default="add-message", description="Command type")
    message: UserMessage = Field(..., description="User message")


class AddToolResultCommand(BaseModel):
    """Command to add a tool result to the conversation."""
    type: str = Field(default="add-tool-result", description="Command type")
    toolCallId: str = Field(..., description="ID of the tool call")
    toolName: Optional[str] = Field(None, description="Name of the tool")
    result: Dict[str, Any] = Field(..., description="Tool execution result")


class ChatRequest(BaseModel):
    """Request payload for the chat endpoint."""
    commands: List[Union[AddMessageCommand, AddToolResultCommand]] = Field(
        ..., description="List of commands to execute"
    )
    system: Optional[str] = Field(None, description="System prompt")
    tools: Optional[Dict[str, Any]] = Field(None, description="Available tools")
    runConfig: Optional[Dict[str, Any]] = Field(None, description="Run configuration")
    state: Optional[Dict[str, Any]] = Field(None, description="State")


async def run_agent(
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    system: Optional[str] = None,
) -> AsyncGenerator[tuple[str, Any], None]:
    """
    Async generator that runs the agent loop.

    Yields events as tuples of (event_type, data):
    - ("text_delta", str): Text chunk to stream
    - ("tool_call_start", {"id": str, "name": str}): Start of a tool call
    - ("tool_call_args", {"id": str, "args": dict}): Complete tool call arguments
    - ("tool_result", {"id": str, "name": str, "result": any}): Tool execution result

    The loop continues until the model produces a response with no tool calls.
    """
    client = AsyncAnthropic()

    while True:
        # Build the API request
        request_kwargs = {
            "model": settings.ANTHROPIC_MODEL,
            "max_tokens": settings.MAX_TOKENS,
            "messages": messages,
        }

        if tools:
            request_kwargs["tools"] = tools

        if system:
            request_kwargs["system"] = system

        # Track tool calls in the current response
        current_tool_calls: Dict[int, Dict[str, Any]] = {}
        text_content = ""

        # Stream the response
        async with client.messages.stream(**request_kwargs) as stream:
            async for event in stream:
                # Handle text deltas
                if event.type == "content_block_delta":
                    if hasattr(event.delta, "text"):
                        text_content += event.delta.text
                        yield ("text_delta", event.delta.text)
                    elif hasattr(event.delta, "partial_json"):
                        # Tool input is being streamed - accumulate it
                        pass

                # Handle content block start (text or tool_use)
                elif event.type == "content_block_start":
                    if event.content_block.type == "tool_use":
                        tool_id = event.content_block.id
                        tool_name = event.content_block.name
                        current_tool_calls[event.index] = {
                            "id": tool_id,
                            "name": tool_name,
                            "input": {}
                        }
                        yield ("tool_call_start", {"id": tool_id, "name": tool_name})

            # Get the final message to extract complete tool calls
            final_message = await stream.get_final_message()

        # Extract tool calls from the final message
        tool_use_blocks = [
            block for block in final_message.content
            if block.type == "tool_use"
        ]

        if not tool_use_blocks:
            # No tool calls - we're done
            break

        # Yield complete tool call arguments
        for block in tool_use_blocks:
            yield ("tool_call_args", {
                "id": block.id,
                "name": block.name,
                "args": block.input
            })

        # Add assistant message to history (with both text and tool_use blocks)
        assistant_content = []
        for block in final_message.content:
            if block.type == "text":
                assistant_content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                assistant_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input
                })

        messages.append({"role": "assistant", "content": assistant_content})

        # Execute tools and collect results
        tool_results = []
        for block in tool_use_blocks:
            result = await execute_tool(block.name, block.input)
            yield ("tool_result", {
                "id": block.id,
                "name": block.name,
                "result": result
            })
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result) if isinstance(result, dict) else str(result)
            })

        # Add tool results to messages
        messages.append({"role": "user", "content": tool_results})


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    print("Starting Assistant Transport Backend (Direct Anthropic SDK)...")
    yield
    print("Shutting down Assistant Transport Backend...")


# Create FastAPI app
app = FastAPI(
    title="Assistant Transport Backend",
    description="A server implementing the assistant-transport protocol with direct Anthropic SDK",
    version="0.2.0",
    lifespan=lifespan,
)

# Configure CORS
cors_origins = settings.cors_origins_list
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)


@app.post("/assistant")
async def chat_endpoint(request: ChatRequest):
    """Chat endpoint using direct Anthropic SDK with streaming."""

    async def run_callback(controller):
        """Callback function for the run controller."""
        # Initialize controller state if needed
        if controller.state is None:
            controller.state = {}
        if "messages" not in controller.state:
            controller.state["messages"] = []

        # Build input messages in Anthropic format for the SDK
        input_messages = []
        print("Processing chat request commands...")
        print(f"Commands: {request.commands}")

        # Process commands
        for command in request.commands:
            if command.type == "add-message":
                # Extract text from parts
                text_parts = [
                    part.text for part in command.message.parts
                    if part.type == "text" and part.text
                ]
                if text_parts:
                    text = " ".join(text_parts)
                    # Add to input_messages in Anthropic format for SDK
                    input_messages.append({"role": "user", "content": text})
                    # Add to state in LangChain format for frontend
                    controller.state["messages"].append(create_human_message(text))
            elif command.type == "add-tool-result":
                # Handle tool results from frontend-executed tools
                print("Adding tool result to conversation")
                print(f"Tool Result: {command.result}")
                result_content = json.dumps(command.result)
                # Add to input_messages in Anthropic format for SDK
                input_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": command.toolCallId,
                        "content": result_content
                    }]
                })
                # Add to state in LangChain format for frontend
                controller.state["messages"].append(
                    create_tool_message(command.toolCallId, result_content)
                )

        # Convert existing conversation history to Anthropic format
        # and prepend to input_messages for full context
        # Note: controller.state["messages"] is a proxy object, convert to list for slicing
        state_messages = list(controller.state["messages"])
        num_new_messages = len(input_messages)
        if num_new_messages > 0:
            # Exclude the newly added messages from history (they're in input_messages)
            history = state_messages[:-num_new_messages]
        else:
            # No new messages added, use full state as history
            history = state_messages
        history_messages = convert_langchain_to_anthropic(history)
        full_messages = history_messages + input_messages
        print(f"Full conversation: {len(history_messages)} history + {len(input_messages)} new = {len(full_messages)} total")

        # Track current tool call for streaming
        current_tool_call = None
        # Track the current assistant message for LangChain format
        current_ai_message_index = None
        current_ai_text = ""
        current_tool_calls = []

        # Run the agent loop with full conversation history
        async for event_type, data in run_agent(full_messages, TOOLS, request.system):
            if event_type == "text_delta":
                # Initialize AI message if this is the first text delta
                if current_ai_message_index is None:
                    current_ai_message_index = len(controller.state["messages"])
                    controller.state["messages"].append(create_ai_message(""))

                # Accumulate text and update state
                current_ai_text += data
                controller.state["messages"][current_ai_message_index]["content"] = current_ai_text

            elif event_type == "tool_call_start":
                # Start a new tool call
                current_tool_call = await controller.add_tool_call(data["name"], data["id"])

                # Initialize AI message if not already created
                if current_ai_message_index is None:
                    current_ai_message_index = len(controller.state["messages"])
                    controller.state["messages"].append(create_ai_message(""))

                # Add tool call placeholder to the list
                current_tool_calls.append({
                    "id": data["id"],
                    "name": data["name"],
                    "args": {}
                })
                # Update state with tool_calls
                controller.state["messages"][current_ai_message_index]["tool_calls"] = current_tool_calls

            elif event_type == "tool_call_args":
                # Stream args as JSON text
                if current_tool_call:
                    current_tool_call.append_args_text(json.dumps(data["args"]))

                # Update the tool call args in state
                for tc in current_tool_calls:
                    if tc["id"] == data["id"]:
                        tc["args"] = data["args"]
                        break
                if current_ai_message_index is not None:
                    controller.state["messages"][current_ai_message_index]["tool_calls"] = current_tool_calls

            elif event_type == "tool_result":
                # Set the tool result
                if current_tool_call:
                    current_tool_call.set_response(data["result"])
                    current_tool_call = None

                # Add tool result to state in LangChain format
                result = data["result"]
                result_content = json.dumps(result) if isinstance(result, dict) else str(result)
                controller.state["messages"].append(
                    create_tool_message(data["id"], result_content)
                )

                # Reset for next assistant turn
                current_ai_message_index = None
                current_ai_text = ""
                current_tool_calls = []

    # Create streaming response using assistant-stream
    stream = create_run(run_callback, state=request.state)

    return DataStreamResponse(stream)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "assistant-transport-backend"}


def main():
    """Main entry point for running the server."""
    host = settings.HOST
    port = settings.PORT
    debug = settings.DEBUG
    log_level = settings.LOG_LEVEL.lower()

    print(f"Starting Assistant Transport Backend on {host}:{port}")
    print(f"Debug mode: {debug}")
    print(f"CORS origins: {settings.cors_origins_list}")

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=debug,
        log_level=log_level,
        access_log=True,
    )


if __name__ == "__main__":
    main()
