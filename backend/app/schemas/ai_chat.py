"""AI Chat 对话 Schema。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AIChatContentPart(BaseModel):
    """多模态消息内容片段。"""

    model_config = ConfigDict(populate_by_name=True)

    type: Literal["text", "image_url"]
    text: str | None = None
    image_url: dict[str, str] | None = Field(default=None, alias="imageUrl")


class AIChatCompletionMessage(BaseModel):
    """AI 聊天消息。"""

    model_config = ConfigDict(populate_by_name=True)

    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[AIChatContentPart]
    images: list[str] = Field(default_factory=list)


class AIChatCompletionRequest(BaseModel):
    """AI 聊天完成请求。"""

    model_config = ConfigDict(populate_by_name=True)

    model: str | None = Field(default=None)
    messages: list[AIChatCompletionMessage]
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=1024, ge=1, le=32000, alias="maxTokens")
    stream: bool = False
