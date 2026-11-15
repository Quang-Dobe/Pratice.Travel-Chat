import { HttpResponse, http } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import {
  ApiError,
  getConversation,
  loadAppData,
  NotInvitedError,
  parseSseFrames,
  type StreamEvent,
  sendMessage,
  UnauthorizedError,
} from './api';

const encoder = new TextEncoder();

const planCard = {
  id: 'card1',
  status: 'open',
  flights: [
    {
      id: 'f1',
      summary: 'Direct',
      priceUsd: 320.5,
      deepLink: 'https://example.com/f1',
      legs: [],
      location: null,
    },
  ],
  hotels: [
    {
      id: 'h1',
      summary: 'Hotel one',
      priceUsd: 400,
      deepLink: 'https://example.com/h1',
      legs: [],
      location: null,
    },
  ],
  picked: { flightId: 'f1', hotelId: 'h1' },
  days: [
    {
      date: '2026-11-01',
      activities: ['Temple'],
      weather: 'no forecast available yet',
      costUsd: 90,
      stops: [],
    },
  ],
  reviewPins: [],
  budget: {
    flightUsd: 400,
    hotelUsd: 400,
    dailyUsd: 90,
    totalUsd: 890,
    budgetUsd: null,
    overBudget: false,
  },
};

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function streamOf(chunks: string[]) {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
}

function serveStream(chunks: string[]) {
  server.use(
    http.post(
      '*/api/conversations/c1/messages',
      () =>
        new HttpResponse(streamOf(chunks), {
          headers: { 'Content-Type': 'text/event-stream' },
        }),
    ),
  );
}

async function collect(): Promise<StreamEvent[]> {
  const events: StreamEvent[] = [];
  for await (const event of sendMessage('c1', 'hi')) {
    events.push(event);
  }
  return events;
}

describe('parseSseFrames', () => {
  it('parses several frames in one chunk', () => {
    const { events, rest } = parseSseFrames(
      'event: token\ndata: {"text":"a"}\n\nevent: token\ndata: {"text":"b"}\n\nevent: done\ndata: {}\n\n',
    );

    expect(events).toEqual([
      { type: 'token', text: 'a' },
      { type: 'token', text: 'b' },
      { type: 'done' },
    ]);
    expect(rest).toBe('');
  });

  it('keeps an incomplete frame as the rest', () => {
    const first = parseSseFrames('event: token\ndata: {"te');
    expect(first.events).toEqual([]);

    const second = parseSseFrames(`${first.rest}xt":"a"}\n\n`);
    expect(second.events).toEqual([{ type: 'token', text: 'a' }]);
  });

  it('handles CRLF line endings split across chunks', () => {
    const first = parseSseFrames('event: token\r');
    const second = parseSseFrames(`${first.rest}\ndata: {"text":"a"}\r\n\r\n`);

    expect(second.events).toEqual([{ type: 'token', text: 'a' }]);
  });

  it('ignores comment lines and comment-only frames', () => {
    const { events } = parseSseFrames(
      ': keep-alive\n\n: note\nevent: token\ndata: {"text":"a"}\n\n',
    );

    expect(events).toEqual([{ type: 'token', text: 'a' }]);
  });

  it('parses the tool_call, plan_card, pills and notice events', () => {
    const { events } = parseSseFrames(
      [
        'event: tool_call\ndata: {"kind":"retrieve","name":"flights__search","status":"started"}\n\n',
        `event: plan_card\ndata: ${JSON.stringify(planCard)}\n\n`,
        'event: pills\ndata: {"items":[{"text":"Approve","action":"approve"},{"text":"Reject","action":"reject"},{"text":"Add a hotel","action":null}]}\n\n',
        'event: notice\ndata: {"message":"The AI model is not configured."}\n\n',
      ].join(''),
    );

    expect(events).toEqual([
      { type: 'tool_call', kind: 'retrieve', name: 'flights__search', status: 'started' },
      { type: 'plan_card', card: planCard },
      {
        type: 'pills',
        items: [
          { text: 'Approve', action: 'approve' },
          { text: 'Reject', action: 'reject' },
          { text: 'Add a hotel', action: null },
        ],
      },
      { type: 'notice', message: 'The AI model is not configured.' },
    ]);
  });

  it('accepts an empty pills list', () => {
    expect(parseSseFrames('event: pills\ndata: {"items":[]}\n\n').events).toEqual([
      { type: 'pills', items: [] },
    ]);
  });

  it('ignores an unknown event, even with a payload that is not json', () => {
    const { events } = parseSseFrames(
      'event: approval\ndata: {"id":"a1","stop":"brief","options":[]}\n\nevent: future\ndata: nope\n\nevent: token\ndata: {"text":"a"}\n\n',
    );

    expect(events).toEqual([{ type: 'token', text: 'a' }]);
  });

  it('parses a sources event, keeps https links only and caps them at three', () => {
    const links = [
      { title: 'One', url: 'https://example.com/1' },
      { title: 'Plain', url: 'http://example.com/2' },
      { title: 'Script', url: 'javascript:alert(1)' },
      { title: 'Two', url: 'https://example.com/3' },
      { title: 'Three', url: 'https://example.com/4' },
      { title: 'Four', url: 'https://example.com/5' },
    ];
    const items = [
      { subject: 'Ramen Bar', links },
      { subject: 'Only bad', links: [{ title: 'Bad', url: 'http://example.com' }] },
    ];

    const { events } = parseSseFrames(`event: sources\ndata: ${JSON.stringify({ items })}\n\n`);

    expect(events).toEqual([
      {
        type: 'sources',
        items: [
          {
            subject: 'Ramen Bar',
            links: [
              { title: 'One', url: 'https://example.com/1' },
              { title: 'Two', url: 'https://example.com/3' },
              { title: 'Three', url: 'https://example.com/4' },
            ],
          },
        ],
      },
    ]);
  });

  it('parses an artifact event', () => {
    const artifact = {
      id: 'e1',
      format: 'docx',
      fileName: 'trip.docx',
      url: '/api/conversations/c1/exports/e1',
      draft: true,
    };

    const { events } = parseSseFrames(`event: artifact\ndata: ${JSON.stringify(artifact)}\n\n`);

    expect(events).toEqual([{ type: 'artifact', artifact }]);
  });

  it('rejects an artifact url outside the conversations api or with a bad format', () => {
    const artifact = {
      id: 'e1',
      format: 'docx',
      fileName: 'trip.docx',
      url: '/api/conversations/c1/exports/e1',
      draft: false,
    };
    for (const bad of [
      { url: 'https://evil.example.com/trip.docx' },
      { url: '/other/c1/exports/e1' },
      { format: 'exe' },
    ]) {
      expect(() =>
        parseSseFrames(`event: artifact\ndata: ${JSON.stringify({ ...artifact, ...bad })}\n\n`),
      ).toThrow();
    }
  });

  it('rejects a non-https deepLink and a bad tool_call status', () => {
    for (const deepLink of ['http://example.com', 'javascript:alert(1)']) {
      const card = { ...planCard, flights: [{ ...planCard.flights[0], deepLink }] };
      expect(() => parseSseFrames(`event: plan_card\ndata: ${JSON.stringify(card)}\n\n`)).toThrow();
    }
    expect(() =>
      parseSseFrames(
        'event: tool_call\ndata: {"kind":"retrieve","name":"flights__search","status":"failed"}\n\n',
      ),
    ).toThrow();
    expect(() =>
      parseSseFrames(
        'event: tool_call\ndata: {"kind":"flights","name":"search_flights","status":"failed"}\n\n',
      ),
    ).toThrow();
  });

  it('parses a map_pins event', () => {
    const pins = [{ name: 'Ramen Bar', kind: 'restaurant', lat: 35.68, lon: 139.76 }];

    const { events } = parseSseFrames(
      `event: map_pins\ndata: ${JSON.stringify({ cardId: 'card1', pins })}\n\n`,
    );

    expect(events).toEqual([{ type: 'map_pins', cardId: 'card1', pins }]);
  });

  it('rejects a map_pins event with a bad kind or an out-of-range coordinate', () => {
    for (const pin of [
      { name: 'A', kind: 'museum', lat: 1, lon: 1 },
      { name: 'A', kind: 'place', lat: 91, lon: 1 },
      { name: 'A', kind: 'place', lat: 1, lon: -181 },
    ]) {
      expect(() =>
        parseSseFrames(
          `event: map_pins\ndata: ${JSON.stringify({ cardId: 'card1', pins: [pin] })}\n\n`,
        ),
      ).toThrow();
    }
  });

  it('parses the map fields of a plan card', () => {
    const leg = {
      from: { code: 'HND', name: 'Haneda', lat: 35.55, lon: 139.78 },
      to: { code: 'SIN', name: 'Changi' },
      departs: '2026-11-01T08:10:00',
      arrives: '2026-11-01T14:25:00',
    };
    const card = {
      ...planCard,
      flights: [{ ...planCard.flights[0], legs: [leg] }],
      hotels: [{ ...planCard.hotels[0], location: { lat: 35.7, lon: 139.7 } }],
      days: [{ ...planCard.days[0], stops: [{ name: 'Temple', lat: 35.71, lon: 139.8 }] }],
      reviewPins: [{ name: 'Ramen Bar', kind: 'restaurant', lat: 35.68, lon: 139.76 }],
    };

    const { events } = parseSseFrames(`event: plan_card\ndata: ${JSON.stringify(card)}\n\n`);

    expect(events).toEqual([
      {
        type: 'plan_card',
        card: {
          ...card,
          flights: [
            { ...card.flights[0], legs: [{ ...leg, to: { ...leg.to, lat: null, lon: null } }] },
          ],
        },
      },
    ]);
  });

  it('still parses a sprint-1-shaped card and defaults the map fields', () => {
    const { flights, hotels, days, reviewPins, ...rest } = planCard;
    void reviewPins;
    const old = {
      ...rest,
      flights: flights.map(({ id, summary, priceUsd, deepLink }) => ({
        id,
        summary,
        priceUsd,
        deepLink,
      })),
      hotels: hotels.map(({ id, summary, priceUsd, deepLink }) => ({
        id,
        summary,
        priceUsd,
        deepLink,
      })),
      days: days.map(({ date, activities, weather, costUsd }) => ({
        date,
        activities,
        weather,
        costUsd,
      })),
    };

    const { events } = parseSseFrames(`event: plan_card\ndata: ${JSON.stringify(old)}\n\n`);

    expect(events).toEqual([{ type: 'plan_card', card: planCard }]);
  });

  it('rejects malformed data', () => {
    expect(() => parseSseFrames('event: token\ndata: not-json\n\n')).toThrow();
    expect(() => parseSseFrames('event: token\ndata: {"text":1}\n\n')).toThrow();
  });
});

describe('getConfig', () => {
  async function freshGetConfig() {
    vi.resetModules();
    return (await import('./api')).getConfig;
  }

  it('fetches /config.json once and memoises it', async () => {
    let calls = 0;
    server.use(
      http.get('*/config.json', () => {
        calls += 1;
        return HttpResponse.json({ googleMapsApiKey: 'key-1' });
      }),
    );
    const getConfig = await freshGetConfig();

    expect(await getConfig()).toEqual({ googleMapsApiKey: 'key-1' });
    expect(await getConfig()).toEqual({ googleMapsApiKey: 'key-1' });
    expect(calls).toBe(1);
  });

  it.each([
    ['a server error', () => new HttpResponse(null, { status: 500 })],
    ['a network failure', () => HttpResponse.error()],
    ['malformed json', () => new HttpResponse('nope', { status: 200 })],
  ])('falls back to an empty key on %s', async (_name, respond) => {
    server.use(http.get('*/config.json', respond));
    const getConfig = await freshGetConfig();

    expect(await getConfig()).toEqual({ googleMapsApiKey: '' });
  });
});

describe('loadAppData', () => {
  const me = { id: 'u1', name: 'Ada', email: 'ada@example.com' };

  it('returns the profile and history', async () => {
    server.use(
      http.get('*/api/users/me', () => HttpResponse.json(me)),
      http.get('*/api/conversations', () => HttpResponse.json({ items: [] })),
    );

    expect(await loadAppData()).toEqual({ me, conversations: [] });
  });

  it('reports not invited only when the profile is 404', async () => {
    server.use(
      http.get('*/api/users/me', () => new HttpResponse(null, { status: 404 })),
      http.get('*/api/conversations', () => HttpResponse.json({ items: [] })),
    );

    await expect(loadAppData()).rejects.toBeInstanceOf(NotInvitedError);
  });

  it('surfaces a 404 from the conversation list as ApiError, not not-invited', async () => {
    server.use(
      http.get('*/api/users/me', () => HttpResponse.json(me)),
      http.get('*/api/conversations', () => new HttpResponse(null, { status: 404 })),
    );

    const failure = await loadAppData().catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).not.toBeInstanceOf(NotInvitedError);
  });

  it('reports unauthorized when either call is 401', async () => {
    server.use(
      http.get('*/api/users/me', () => HttpResponse.json(me)),
      http.get('*/api/conversations', () => new HttpResponse(null, { status: 401 })),
    );

    await expect(loadAppData()).rejects.toBeInstanceOf(UnauthorizedError);
  });
});

describe('getConversation', () => {
  it('defaults the new fields for a payload without them', async () => {
    server.use(
      http.get('*/api/conversations/c1', () =>
        HttpResponse.json({
          id: 'c1',
          title: 'Hello',
          createdAt: '2026-10-01T00:00:00Z',
          messages: [{ role: 'assistant', text: 'Hi' }],
        }),
      ),
    );

    expect(await getConversation('c1')).toEqual({
      id: 'c1',
      title: 'Hello',
      createdAt: '2026-10-01T00:00:00Z',
      messages: [
        {
          role: 'assistant',
          text: 'Hi',
          toolCalls: [],
          planCard: null,
          pills: [],
          sources: [],
          artifacts: [],
        },
      ],
    });
  });

  it('parses sources and artifacts on a message', async () => {
    const artifact = {
      id: 'e1',
      format: 'xlsx',
      fileName: 'trip.xlsx',
      url: '/api/conversations/c1/exports/e1',
      draft: false,
    };
    const sources = [
      { subject: 'Ramen Bar', links: [{ title: 'Guide', url: 'https://x.test/a' }] },
    ];
    server.use(
      http.get('*/api/conversations/c1', () =>
        HttpResponse.json({
          id: 'c1',
          title: 'Hello',
          createdAt: '2026-10-01T00:00:00Z',
          messages: [{ role: 'assistant', text: 'Hi', sources, artifacts: [artifact] }],
        }),
      ),
    );

    const conversation = await getConversation('c1');

    expect(conversation.messages[0]?.sources).toEqual(sources);
    expect(conversation.messages[0]?.artifacts).toEqual([artifact]);
  });

  it('parses tool calls, the plan card and pill objects', async () => {
    server.use(
      http.get('*/api/conversations/c1', () =>
        HttpResponse.json({
          id: 'c1',
          title: 'Hello',
          createdAt: '2026-10-01T00:00:00Z',
          messages: [
            {
              role: 'assistant',
              text: 'Pick one',
              toolCalls: [{ name: 'hotels__search', kind: 'retrieve' }],
              planCard,
              pills: [{ text: 'Approve', action: 'approve' }, { text: 'Cheaper' }],
            },
          ],
        }),
      ),
    );

    const conversation = await getConversation('c1');

    expect(conversation).not.toHaveProperty('pendingApprovalId');
    expect(conversation.messages[0]).toEqual({
      role: 'assistant',
      text: 'Pick one',
      toolCalls: [{ name: 'hotels__search', kind: 'retrieve', status: 'finished' }],
      planCard,
      pills: [
        { text: 'Approve', action: 'approve' },
        { text: 'Cheaper', action: null },
      ],
      sources: [],
      artifacts: [],
    });
  });
});

describe('sendMessage', () => {
  async function drain(action?: Parameters<typeof sendMessage>[2]): Promise<unknown> {
    let received: unknown;
    server.use(
      http.post('*/api/conversations/c1/messages', async ({ request }) => {
        received = await request.json();
        return new HttpResponse(streamOf(['event: done\ndata: {}\n\n']), {
          headers: { 'Content-Type': 'text/event-stream' },
        });
      }),
    );
    for await (const event of sendMessage('c1', 'Approve', action)) {
      void event;
    }
    return received;
  }

  it('posts a null action for a plain message', async () => {
    expect(await drain()).toEqual({ text: 'Approve', action: null });
  });

  it('posts the approve action with the card and both picks', async () => {
    const action = { type: 'approve', cardId: 'card1', flightId: 'f1', hotelId: 'h1' } as const;

    expect(await drain(action)).toEqual({ text: 'Approve', action });
  });

  it('posts the reject action with the card id', async () => {
    const action = { type: 'reject', cardId: 'card1' } as const;

    expect(await drain(action)).toEqual({ text: 'Approve', action });
  });

  it('yields frames that are split across chunks', async () => {
    serveStream([
      'event: tok',
      'en\ndata: {"text":"Hel',
      'lo"}\n',
      '\nevent: token\ndata: {"text":" you"}\n\nevent: done\ndata: {}\n\n',
    ]);

    expect(await collect()).toEqual([
      { type: 'token', text: 'Hello' },
      { type: 'token', text: ' you' },
      { type: 'done' },
    ]);
  });

  it('ignores a comment line', async () => {
    serveStream([': ping\n\nevent: token\ndata: {"text":"a"}\n\nevent: done\ndata: {}\n\n']);

    expect(await collect()).toEqual([{ type: 'token', text: 'a' }, { type: 'done' }]);
  });

  it('ends the stream at done', async () => {
    serveStream([
      'event: token\ndata: {"text":"a"}\n\nevent: done\ndata: {}\n\nevent: token\ndata: {"text":"late"}\n\n',
    ]);

    expect(await collect()).toEqual([{ type: 'token', text: 'a' }, { type: 'done' }]);
  });

  it('rejects a stream that closes without done', async () => {
    serveStream(['event: token\ndata: {"text":"a"}\n\n']);

    await expect(collect()).rejects.toThrow('Stream ended before done');
  });

  it('rejects malformed data', async () => {
    serveStream(['event: token\ndata: {broken\n\n']);

    await expect(collect()).rejects.toThrow();
  });

  it.each([404, 422])('surfaces %i as ApiError before any frame', async (status) => {
    server.use(
      http.post('*/api/conversations/c1/messages', () => new HttpResponse(null, { status })),
    );

    await expect(collect()).rejects.toMatchObject({ name: 'ApiError', status });
    await expect(collect()).rejects.toBeInstanceOf(ApiError);
  });
});
