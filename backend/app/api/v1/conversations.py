"""AI 共创工作台会话 API：独立聊天记录。"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import require_auth
from app.core.identity import auth_user_id
from app.db.session import get_db
from app.models.conversation import Conversation, ConversationMessage
from app.schemas.conversation import (
    ConversationCreateRequest,
    ConversationDetailData,
    ConversationListData,
    ConversationResponse,
    MessageCreateRequest,
    MessageResponse,
)
from app.utils.response import success_response

router = APIRouter(prefix="/conversations")


def _message_response(message: ConversationMessage) -> MessageResponse:
    return MessageResponse(
        id=message.id,
        role=message.role,
        text=message.text,
        cardData=message.card_data,
        createdAt=message.created_at,
    )


def _conversation_response(conversation: Conversation) -> ConversationResponse:
    return ConversationResponse(
        id=conversation.id,
        projectId=conversation.project_id,
        title=conversation.title,
        createdAt=conversation.created_at,
        updatedAt=conversation.updated_at,
        messages=[_message_response(m) for m in conversation.messages],
    )


@router.post("", response_model=dict, summary="创建会话")
def create_conversation(
    request: ConversationCreateRequest,
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    user_id = auth_user_id(auth_user)
    conversation = Conversation(
        user_id=user_id,
        project_id=request.project_id,
        title=request.title,
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return success_response(
        data=_conversation_response(conversation).model_dump(by_alias=True),
        message="会话已创建",
    )


@router.get("", response_model=dict, summary="获取会话列表")
def list_conversations(
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    user_id = auth_user_id(auth_user)
    conversations = db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
        .limit(100)
    ).all()
    data = ConversationListData(
        conversations=[_conversation_response(c) for c in conversations]
    )
    return success_response(
        data=data.model_dump(by_alias=True),
        message="会话列表读取成功",
    )


@router.get("/{conversation_id}", response_model=dict, summary="获取会话详情与消息")
def get_conversation(
    conversation_id: UUID,
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    user_id = auth_user_id(auth_user)
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    data = ConversationDetailData(conversation=_conversation_response(conversation))
    return success_response(
        data=data.model_dump(by_alias=True),
        message="会话详情读取成功",
    )


@router.post("/{conversation_id}/messages", response_model=dict, summary="追加消息")
def append_message(
    conversation_id: UUID,
    request: MessageCreateRequest,
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    user_id = auth_user_id(auth_user)
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    message = ConversationMessage(
        conversation_id=conversation_id,
        role=request.role,
        text=request.text,
        card_data=request.card_data,
    )
    db.add(message)
    from datetime import datetime, timezone

    conversation.updated_at = datetime.now(timezone.utc)
    if conversation.title == "新对话" and request.role == "user" and request.text.strip():
        conversation.title = request.text.strip()[:40]
    db.commit()
    db.refresh(message)
    return success_response(
        data=_message_response(message).model_dump(by_alias=True),
        message="消息已追加",
    )


@router.delete("/{conversation_id}", response_model=dict, summary="删除会话")
def delete_conversation(
    conversation_id: UUID,
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    user_id = auth_user_id(auth_user)
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    db.execute(
        delete(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation_id
        )
    )
    db.delete(conversation)
    db.commit()
    return success_response(message="会话已删除")
