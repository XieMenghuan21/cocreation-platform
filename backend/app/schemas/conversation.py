"""会话与聊天消息接口 schema。"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConversationCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    project_id: str | None = Field(default=None, alias="projectId", max_length=160)
    title: str = Field(default="新对话", max_length=255)


class MessageCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    role: str = Field(default="user", max_length=32)
    text: str = Field(default="", max_length=20000)
    card_data: dict[str, object] = Field(default_factory=dict, alias="cardData")


class MessageResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    role: str
    text: str
    card_data: dict[str, object] = Field(alias="cardData")
    created_at: datetime = Field(alias="createdAt")


class ConversationResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    project_id: str | None = Field(alias="projectId")
    title: str
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    messages: list[MessageResponse] = Field(default_factory=list)


class ConversationListData(BaseModel):
    conversations: list[ConversationResponse]


class ConversationDetailData(BaseModel):
    conversation: ConversationResponse
