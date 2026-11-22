import {
  type Action,
  type AppData,
  type Conversation,
  ConversationItem,
  ConversationList,
  ConversationSchema,
  Me,
  SessionTimeLeft,
  StarterSetting,
  Starters,
} from './schemas';
import { parseSseFrames, type StreamEvent } from './sse';

export class UnauthorizedError extends Error {
  constructor() {
    super('Unauthorized');
    this.name = 'UnauthorizedError';
  }
}

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number) {
    super(`Request failed with status ${status}`);
    this.name = 'ApiError';
    this.status = status;
  }
}

export class NotInvitedError extends Error {
  constructor() {
    super('Not invited');
    this.name = 'NotInvitedError';
  }
}

export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const url = new URL(path, window.location.origin);
  const response = await fetch(url, { ...init, credentials: 'same-origin' });
  if (response.status === 401) {
    throw new UnauthorizedError();
  }
  if (!response.ok) {
    throw new ApiError(response.status);
  }
  return response;
}

export async function getMe(): Promise<Me> {
  const response = await apiFetch('/api/users/me');
  return Me.parse(await response.json());
}

export async function getSessionTimeLeft(): Promise<SessionTimeLeft> {
  const response = await apiFetch('/api/session');
  return SessionTimeLeft.parse(await response.json());
}

export async function listConversations(): Promise<ConversationItem[]> {
  const response = await apiFetch('/api/conversations');
  return ConversationList.parse(await response.json()).items;
}

export async function logout(): Promise<void> {
  await apiFetch('/auth/logout', { method: 'POST' });
}

export async function loadAppData(): Promise<AppData> {
  const [me, conversations] = await Promise.all([
    getMe().catch((error: unknown) => {
      throw error instanceof ApiError && error.status === 404 ? new NotInvitedError() : error;
    }),
    listConversations(),
  ]);
  return { me, conversations };
}

export async function createConversation(text: string): Promise<ConversationItem> {
  const response = await apiFetch('/api/conversations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  });
  return ConversationItem.parse(await response.json());
}

export async function getConversation(id: string): Promise<Conversation> {
  const response = await apiFetch(`/api/conversations/${encodeURIComponent(id)}`);
  return ConversationSchema.parse(await response.json());
}

export async function getStarters(signal?: AbortSignal): Promise<Starters> {
  const response = await apiFetch('/api/conversations/starters', { signal: signal ?? null });
  return Starters.parse(await response.json());
}

export async function setStarters(enabled: boolean): Promise<boolean> {
  const response = await apiFetch('/api/conversations/starters/setting', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
  return StarterSetting.parse(await response.json()).enabled;
}

export async function clearStarters(): Promise<void> {
  await apiFetch('/api/conversations/starters/history', { method: 'DELETE' });
}

async function* streamEvents(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const response = await apiFetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
    signal: signal ?? null,
  });
  if (!response.body) {
    throw new Error('Response has no body');
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        throw new Error('Stream ended before done');
      }
      buffer += decoder.decode(value, { stream: true });
      const parsed = parseSseFrames(buffer);
      buffer = parsed.rest;
      for (const event of parsed.events) {
        yield event;
        if (event.type === 'done') {
          return;
        }
      }
    }
  } finally {
    await reader.cancel().catch(() => undefined);
  }
}

export function sendMessage(
  id: string,
  text: string,
  action?: Action | null,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  return streamEvents(
    `/api/conversations/${encodeURIComponent(id)}/messages`,
    { text, action: action ?? null },
    signal,
  );
}
