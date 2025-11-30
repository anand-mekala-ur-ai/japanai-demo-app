#!/usr/bin/env python3
"""
Assistant Transport Backend - FastAPI + assistant-stream + Direct Anthropic SDK

This implementation uses the Anthropic SDK directly without third-party agent frameworks
(no LangChain, LangGraph, or similar libraries).
"""

import os
import json
from typing import Dict, Any, List, Optional, Union, AsyncGenerator
from contextlib import asynccontextmanager
import uvicorn

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from anthropic import AsyncAnthropic

from assistant_stream.serialization import DataStreamResponse
from assistant_stream import create_run

from tools import TOOLS, execute_tool

# Load environment variables
load_dotenv()


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
            "model": "claude-sonnet-4-5-20250929",
            "max_tokens": 4096,
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
cors_origins = ["*"]
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
                    input_messages.append({
                        "role": "user",
                        "content": " ".join(text_parts)
                    })
            elif command.type == "add-tool-result":
                # Handle tool results from frontend-executed tools
                print("Adding tool result to conversation")
                print(f"Tool Result: {command.result}")
                input_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": command.toolCallId,
                        "content": json.dumps(command.result)
                    }]
                })

        # Add messages to controller state for persistence
        for message in input_messages:
            controller.state["messages"].append(message)

        # Check if API key is available
        if not os.getenv("ANTHROPIC_API_KEY"):
            print("Warning: No ANTHROPIC_API_KEY found - using mock response")
            # Mock response for testing without API key
            controller.append_text(
                "I would help you, but no ANTHROPIC_API_KEY is configured. "
                "Please set your API key in the .env file."
            )
            return

        # Track current tool call for streaming
        current_tool_call = None
        # Track accumulated text for state persistence
        accumulated_text = ""

        # Run the agent loop
        async for event_type, data in run_agent(input_messages, TOOLS, request.system):
            if event_type == "text_delta":
                accumulated_text += data
                controller.append_text(data)

            elif event_type == "tool_call_start":
                # Start a new tool call
                current_tool_call = await controller.add_tool_call(data["id"], data["name"])

            elif event_type == "tool_call_args":
                # Stream args as JSON text
                if current_tool_call:
                    current_tool_call.append_args_text(json.dumps(data["args"]))

            elif event_type == "tool_result":
                # Set the tool result
                if current_tool_call:
                    current_tool_call.set_response(data["result"])
                    current_tool_call = None

        # Add assistant message to state for multi-turn conversations
        if accumulated_text:
            controller.state["messages"].append({
                "role": "assistant",
                "content": accumulated_text
            })

    # Create streaming response using assistant-stream
    stream = create_run(run_callback, state=request.state)

    return DataStreamResponse(stream)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "assistant-transport-backend"}


def main():
    """Main entry point for running the server."""
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8010"))
    debug = os.getenv("DEBUG", "false").lower() == "true"
    log_level = os.getenv("LOG_LEVEL", "info").lower()

    print(f"Starting Assistant Transport Backend on {host}:{port}")
    print(f"Debug mode: {debug}")
    print(f"CORS origins: {cors_origins}")

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
