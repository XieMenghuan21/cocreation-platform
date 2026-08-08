import { get, post, del, type ApiResponse } from './httpRequest';

export interface ConversationMessage {
  id: number;
  role: 'user' | 'assistant';
  text: string;
  cardData: Record<string, unknown>;
  createdAt: string;
}

export interface Conversation {
  id: string;
  projectId: string | null;
  title: string;
  createdAt: string;
  updatedAt: string;
  messages: ConversationMessage[];
}

export interface CreateConversationPayload {
  projectId?: string | null;
  title?: string;
}

export interface AppendMessagePayload {
  role: 'user' | 'assistant';
  text: string;
  cardData?: Record<string, unknown>;
}

export const conversationService = {
  async create(payload: CreateConversationPayload = {}): Promise<Conversation> {
    const response = await post<Conversation>(
      '/api/v1/conversations',
      {
        projectId: payload.projectId ?? null,
        title: payload.title ?? '新对话',
      },
      { showError: false },
    );
    return response.data;
  },

  async list(): Promise<Conversation[]> {
    const response = await get<{ conversations: Conversation[] }>(
      '/api/v1/conversations',
      undefined,
      { showError: false },
    );
    return response.data.conversations || [];
  },

  async get(conversationId: string): Promise<Conversation> {
    const response = await get<{ conversation: Conversation }>(
      `/api/v1/conversations/${conversationId}`,
      undefined,
      { showError: false },
    );
    return response.data.conversation;
  },

  async append(
    conversationId: string,
    payload: AppendMessagePayload,
  ): Promise<ConversationMessage> {
    const response = await post<ConversationMessage>(
      `/api/v1/conversations/${conversationId}/messages`,
      {
        role: payload.role,
        text: payload.text,
        cardData: payload.cardData ?? {},
      },
      { showError: false },
    );
    return response.data;
  },

  async remove(conversationId: string): Promise<void> {
    await del(`/api/v1/conversations/${conversationId}`, undefined, {
      showError: false,
    });
  },
};
