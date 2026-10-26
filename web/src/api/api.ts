import { z } from 'zod';

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

export const Me = z.object({
  id: z.string(),
  name: z.string(),
  email: z.string(),
});
export type Me = z.infer<typeof Me>;

export const ConversationItem = z.object({
  id: z.string(),
  title: z.string(),
  createdAt: z.string(),
});
export type ConversationItem = z.infer<typeof ConversationItem>;

const ConversationList = z.object({ items: z.array(ConversationItem) });

export const Message = z.object({
  role: z.enum(['user', 'assistant']),
  text: z.string(),
});
export type Message = z.infer<typeof Message>;

export const Conversation = ConversationItem.extend({ messages: z.array(Message) });
export type Conversation = z.infer<typeof Conversation>;

const TokenData = z.object({ text: z.string() });
const DoneData = z.object({});

export type StreamEvent = { type: 'token'; text: string } | { type: 'done' };

export type AppData = {
  me: Me;
  conversations: ConversationItem[];
};

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
  return Conversation.parse(await response.json());
}

function toStreamEvent(event: string, data: string): StreamEvent {
  const payload: unknown = JSON.parse(data);
  switch (event) {
    case 'token':
      return { type: 'token', text: TokenData.parse(payload).text };
    case 'done':
      DoneData.parse(payload);
      return { type: 'done' };
    default:
      throw new Error(`Unknown stream event: ${event}`);
  }
}

function parseSseFrame(frame: string): StreamEvent | null {
  let event = 'message';
  let hasField = false;
  const dataLines: string[] = [];
  for (const line of frame.split('\n')) {
    if (line === '' || line.startsWith(':')) {
      continue;
    }
    const separator = line.indexOf(':');
    const field = separator === -1 ? line : line.slice(0, separator);
    const rawValue = separator === -1 ? '' : line.slice(separator + 1);
    const value = rawValue.startsWith(' ') ? rawValue.slice(1) : rawValue;
    if (field === 'event') {
      event = value;
      hasField = true;
    } else if (field === 'data') {
      dataLines.push(value);
      hasField = true;
    }
  }
  return hasField ? toStreamEvent(event, dataLines.join('\n')) : null;
}

export function parseSseFrames(buffer: string): { events: StreamEvent[]; rest: string } {
  const held = buffer.endsWith('\r') ? '\r' : '';
  const body = held ? buffer.slice(0, -1) : buffer;
  const frames = body.replace(/\r\n?/g, '\n').split('\n\n');
  const rest = frames.pop() ?? '';
  const events: StreamEvent[] = [];
  for (const frame of frames) {
    const parsed = parseSseFrame(frame);
    if (parsed) {
      events.push(parsed);
    }
  }
  return { events, rest: rest + held };
}

export async function* sendMessage(
  id: string,
  text: string,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const response = await apiFetch(`/api/conversations/${encodeURIComponent(id)}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({ text }),
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
