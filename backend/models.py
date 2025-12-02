from typing import Any, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class MessagePart(BaseModel):
    type: str = Field(..., description="The type of message part")
    text: Optional[str] = Field(None, description="Text content")
    image: Optional[str] = Field(None, description="Image URL or data")


class UserMessage(BaseModel):
    role: str = Field(default="user", description="Message role")
    parts: list[MessagePart] = Field(..., description="Message parts")


class AddMessageCommand(BaseModel):
    type: str = Field(default="add-message", description="Command type")
    message: UserMessage = Field(..., description="User message")


class AddToolResultCommand(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: str = Field(default="add-tool-result", description="Command type")
    tool_call_id: str = Field(..., alias="toolCallId", description="ID of the tool call")
    tool_name: Optional[str] = Field(None, alias="toolName", description="Name of the tool")
    result: dict[str, Any] = Field(..., description="Tool execution result")


class ChatRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    commands: list[Union[AddMessageCommand, AddToolResultCommand]] = Field(
        ..., description="List of commands to execute"
    )
    system: Optional[str] = Field(None, description="System prompt")
    tools: Optional[dict[str, Any]] = Field(None, description="Available tools")
    run_config: Optional[dict[str, Any]] = Field(
        None, alias="runConfig", description="Run configuration"
    )
    state: Optional[dict[str, Any]] = Field(None, description="State")


class SearchProductsInput(BaseModel):
    query: str = Field(description="Search term for products (e.g., 'iPhone 15')")
    limit: int = Field(default=3, description="Maximum number of results to return")
