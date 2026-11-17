import type { Message, ReviewPin, StreamEvent } from '../api/api';

export type ChatMessage = Message & { notice?: string };

export function newMessage(role: Message['role'], text = ''): ChatMessage {
  return {
    role,
    text,
    toolCalls: [],
    planCard: null,
    pills: [],
    sources: [],
    artifacts: [],
  };
}

export function latestCardIndex(messages: ChatMessage[]): number {
  return messages.findLastIndex((message) => message.planCard !== null);
}

export function applyEvent(message: ChatMessage, event: StreamEvent): ChatMessage {
  switch (event.type) {
    case 'token':
      return { ...message, text: message.text + event.text };
    case 'tool_call': {
      const { kind, name, status } = event;
      if (status === 'started') {
        return { ...message, toolCalls: [...message.toolCalls, { kind, name, status }] };
      }
      const openIndex = message.toolCalls.findLastIndex(
        (call) => call.name === name && call.status === 'started',
      );
      if (openIndex === -1) {
        return { ...message, toolCalls: [...message.toolCalls, { kind, name, status }] };
      }
      return {
        ...message,
        toolCalls: message.toolCalls.map((call, index) =>
          index === openIndex ? { ...call, status } : call,
        ),
      };
    }
    case 'plan_card':
      return { ...message, planCard: event.card };
    case 'pills':
      return { ...message, pills: event.items };
    case 'notice':
      return { ...message, notice: event.message };
    case 'sources':
      return { ...message, sources: event.items };
    case 'artifact':
      return { ...message, artifacts: [...(message.artifacts ?? []), event.artifact] };
    case 'map_pins':
    case 'done':
      return message;
  }
}

function mergePins(existing: ReviewPin[], incoming: ReviewPin[]): ReviewPin[] {
  const names = new Set(incoming.map((pin) => pin.name));
  return [...existing.filter((pin) => !names.has(pin.name)), ...incoming];
}

export function applyMapPins(
  messages: ChatMessage[],
  cardId: string,
  incoming: ReviewPin[],
): ChatMessage[] {
  return messages.map((message) =>
    message.planCard?.id === cardId
      ? {
          ...message,
          planCard: {
            ...message.planCard,
            reviewPins: mergePins(message.planCard.reviewPins, incoming),
          },
        }
      : message,
  );
}
