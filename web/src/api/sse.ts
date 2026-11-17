import { z } from 'zod';
import {
  ArtifactItem,
  Pill,
  PlanCard,
  ReviewPin,
  type SourceItem,
  SourceList,
  ToolStatus,
} from './schemas';

const TokenData = z.object({ text: z.string() });
const SourcesData = z.object({ items: SourceList });
const DoneData = z.object({});
const ToolCallData = z.object({ kind: z.string(), name: z.string(), status: ToolStatus });
const PillsData = z.object({ items: z.array(Pill) });
const NoticeData = z.object({ message: z.string() });
const MapPinsData = z.object({ cardId: z.string(), pins: z.array(ReviewPin) });

export type StreamEvent =
  | { type: 'token'; text: string }
  | { type: 'tool_call'; kind: string; name: string; status: ToolStatus }
  | { type: 'plan_card'; card: PlanCard }
  | { type: 'pills'; items: Pill[] }
  | { type: 'notice'; message: string }
  | { type: 'map_pins'; cardId: string; pins: ReviewPin[] }
  | { type: 'sources'; items: SourceItem[] }
  | { type: 'artifact'; artifact: ArtifactItem }
  | { type: 'done' };

const EVENT_PARSERS = new Map<string, (payload: unknown) => StreamEvent>([
  ['token', (payload) => ({ type: 'token', text: TokenData.parse(payload).text })],
  ['tool_call', (payload) => ({ type: 'tool_call', ...ToolCallData.parse(payload) })],
  ['plan_card', (payload) => ({ type: 'plan_card', card: PlanCard.parse(payload) })],
  ['pills', (payload) => ({ type: 'pills', items: PillsData.parse(payload).items })],
  ['notice', (payload) => ({ type: 'notice', message: NoticeData.parse(payload).message })],
  ['map_pins', (payload) => ({ type: 'map_pins', ...MapPinsData.parse(payload) })],
  ['sources', (payload) => ({ type: 'sources', items: SourcesData.parse(payload).items })],
  ['artifact', (payload) => ({ type: 'artifact', artifact: ArtifactItem.parse(payload) })],
  [
    'done',
    (payload) => {
      DoneData.parse(payload);
      return { type: 'done' };
    },
  ],
]);

function toStreamEvent(event: string, data: string): StreamEvent | null {
  const parse = EVENT_PARSERS.get(event);
  if (!parse) {
    return null;
  }
  return parse(JSON.parse(data));
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
