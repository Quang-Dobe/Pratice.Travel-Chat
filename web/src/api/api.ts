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

function isHttpsUrl(value: string): boolean {
  try {
    return new URL(value).protocol === 'https:';
  } catch {
    return false;
  }
}

export const ToolStatus = z.enum(['started', 'finished']);
export type ToolStatus = z.infer<typeof ToolStatus>;

export const ToolCall = z.object({
  name: z.string(),
  kind: z.string(),
  status: ToolStatus.default('finished'),
});
export type ToolCall = z.infer<typeof ToolCall>;

export const NO_FORECAST = 'no forecast available yet';

export const Option = z.object({
  id: z.string(),
  summary: z.string(),
  priceUsd: z.number(),
  deepLink: z.string().refine(isHttpsUrl, 'deepLink must be an https URL'),
});
export type Option = z.infer<typeof Option>;

export const Picked = z.object({ flightId: z.string(), hotelId: z.string() });
export type Picked = z.infer<typeof Picked>;

export const PlanDay = z.object({
  date: z.string(),
  activities: z.array(z.string()).default([]),
  weather: z.string().default(NO_FORECAST),
  costUsd: z.number().default(0),
});
export type PlanDay = z.infer<typeof PlanDay>;

export const PlanBudget = z.object({
  dailyUsd: z.number(),
  totalUsd: z.number(),
  budgetUsd: z.number(),
  overBudget: z.boolean(),
});
export type PlanBudget = z.infer<typeof PlanBudget>;

export const PlanCard = z.object({
  id: z.string(),
  status: z.enum(['open', 'approved', 'replaced']),
  flights: z.array(Option),
  hotels: z.array(Option),
  picked: Picked,
  days: z.array(PlanDay),
  budget: PlanBudget,
});
export type PlanCard = z.infer<typeof PlanCard>;

export const Pill = z.object({
  text: z.string(),
  action: z.enum(['approve', 'reject']).nullable().default(null),
});
export type Pill = z.infer<typeof Pill>;

export type Action =
  | { type: 'approve'; cardId: string; flightId: string; hotelId: string }
  | { type: 'reject'; cardId: string };

export const Message = z.object({
  role: z.enum(['user', 'assistant']),
  text: z.string(),
  toolCalls: z.array(ToolCall).default([]),
  planCard: PlanCard.nullable().default(null),
  pills: z.array(Pill).default([]),
});
export type Message = z.infer<typeof Message>;

export const Conversation = ConversationItem.extend({
  messages: z.array(Message),
});
export type Conversation = z.infer<typeof Conversation>;

const TokenData = z.object({ text: z.string() });
const DoneData = z.object({});
const ToolCallData = z.object({ kind: z.string(), name: z.string(), status: ToolStatus });
const PillsData = z.object({ items: z.array(Pill) });
const NoticeData = z.object({ message: z.string() });

export type StreamEvent =
  | { type: 'token'; text: string }
  | { type: 'tool_call'; kind: string; name: string; status: ToolStatus }
  | { type: 'plan_card'; card: PlanCard }
  | { type: 'pills'; items: Pill[] }
  | { type: 'notice'; message: string }
  | { type: 'done' };

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
    case 'tool_call':
      return { type: 'tool_call', ...ToolCallData.parse(payload) };
    case 'plan_card':
      return { type: 'plan_card', card: PlanCard.parse(payload) };
    case 'pills':
      return { type: 'pills', items: PillsData.parse(payload).items };
    case 'notice':
      return { type: 'notice', message: NoticeData.parse(payload).message };
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
