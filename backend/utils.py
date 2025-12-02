from typing import Any


# Helper functions to convert Anthropic messages to LangChain format
def create_human_message(text: str) -> dict:
    return {"type": "human", "content": text}


def create_ai_message(text: str = "", tool_calls: list[Any] | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"type": "ai", "content": text}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def create_tool_message(tool_call_id: str, content: str) -> dict:
    return {"type": "tool", "content": content, "tool_call_id": tool_call_id}


def convert_langchain_to_anthropic(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    anthropic_messages = []
    i = 0

    while i < len(messages):
        msg = messages[i]
        msg_type = msg.get("type")

        if msg_type == "human":
            anthropic_messages.append({"role": "user", "content": msg.get("content", "")})
            i += 1

        elif msg_type == "ai":
            content = []
            text_content = msg.get("content", "")
            if text_content:
                content.append({"type": "text", "text": text_content})

            tool_calls = msg.get("tool_calls", [])
            for tc in tool_calls:
                content.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id"),
                        "name": tc.get("name"),
                        "input": tc.get("args", {}),
                    }
                )

            if content:
                anthropic_messages.append(
                    {
                        "role": "assistant",
                        "content": content if len(content) > 1 or tool_calls else text_content,
                    }
                )
            i += 1

        elif msg_type == "tool":
            # Collect consecutive tool messages into a single user message
            tool_results = []
            while i < len(messages) and messages[i].get("type") == "tool":
                tool_msg = messages[i]
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_msg.get("tool_call_id"),
                        "content": tool_msg.get("content", ""),
                    }
                )
                i += 1

            anthropic_messages.append({"role": "user", "content": tool_results})
        else:
            # Skip unknown message types
            i += 1

    return anthropic_messages
