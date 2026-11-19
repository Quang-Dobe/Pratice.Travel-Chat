import { describe, expect, it } from 'vitest';
import type { ArtifactItem, ReviewPin } from '../api/schemas';
import type { StreamEvent } from '../api/sse';
import { makeCard } from '../test/planCard';
import { applyEvent, applyMapPins, latestCardIndex, newMessage } from './chatMessages';

const started: StreamEvent = { type: 'tool_call', kind: 'tool', name: 'search', status: 'started' };
const finished: StreamEvent = { ...started, status: 'finished' };

describe('newMessage', () => {
  it('builds a message with the given role and text and empty fields', () => {
    expect(newMessage('user', 'hi')).toEqual({
      role: 'user',
      text: 'hi',
      toolCalls: [],
      planCard: null,
      pills: [],
      sources: [],
      artifacts: [],
    });
    expect(newMessage('assistant').text).toBe('');
  });
});

describe('applyEvent', () => {
  it('appends token text', () => {
    const next = applyEvent(newMessage('assistant', 'Hel'), { type: 'token', text: 'lo' });
    expect(next.text).toBe('Hello');
  });

  it('flips the last open call with that name when finished', () => {
    let message = newMessage('assistant');
    message = applyEvent(message, started);
    message = applyEvent(message, started);
    message = applyEvent(message, finished);
    expect(message.toolCalls.map((call) => call.status)).toEqual(['started', 'finished']);
  });

  it('appends a finished call when none is open', () => {
    const next = applyEvent(newMessage('assistant'), finished);
    expect(next.toolCalls).toEqual([{ kind: 'tool', name: 'search', status: 'finished' }]);
  });

  it('appends artifacts', () => {
    const artifact: ArtifactItem = {
      id: 'a1',
      format: 'csv',
      fileName: 'trip.csv',
      url: '/x',
      draft: false,
    };
    const next = applyEvent(newMessage('assistant'), { type: 'artifact', artifact });
    expect(next.artifacts).toEqual([artifact]);
  });

  it('returns the same object for map_pins and done', () => {
    const message = newMessage('assistant');
    expect(applyEvent(message, { type: 'map_pins', cardId: 'c', pins: [] })).toBe(message);
    expect(applyEvent(message, { type: 'done' })).toBe(message);
  });
});

describe('latestCardIndex', () => {
  it('finds the last message with a plan card, or -1', () => {
    const withCard = { ...newMessage('assistant'), planCard: makeCard() };
    expect(latestCardIndex([withCard, newMessage('user', 'x')])).toBe(0);
    expect(latestCardIndex([newMessage('user', 'x')])).toBe(-1);
  });
});

describe('applyMapPins', () => {
  it('replaces a same-name pin only on the matching card id', () => {
    const oldPin: ReviewPin = { name: 'Cafe', kind: 'restaurant', lat: 1, lon: 1 };
    const keep: ReviewPin = { name: 'Park', kind: 'place', lat: 2, lon: 2 };
    const newPin: ReviewPin = { ...oldPin, lat: 3, lon: 3 };
    const a = {
      ...newMessage('assistant'),
      planCard: makeCard({ id: 'a', reviewPins: [oldPin, keep] }),
    };
    const b = { ...newMessage('assistant'), planCard: makeCard({ id: 'b', reviewPins: [oldPin] }) };
    const result = applyMapPins([a, b], 'a', [newPin]);
    expect(result[0]?.planCard?.reviewPins).toEqual([keep, newPin]);
    expect(result[1]).toBe(b);
  });
});
