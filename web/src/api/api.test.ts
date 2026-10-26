import { HttpResponse, http } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import {
  ApiError,
  loadAppData,
  NotInvitedError,
  parseSseFrames,
  type StreamEvent,
  sendMessage,
  UnauthorizedError,
} from './api';

const encoder = new TextEncoder();

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

  it('rejects malformed data', () => {
    expect(() => parseSseFrames('event: token\ndata: not-json\n\n')).toThrow();
    expect(() => parseSseFrames('event: token\ndata: {"text":1}\n\n')).toThrow();
    expect(() => parseSseFrames('event: other\ndata: {}\n\n')).toThrow();
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

describe('sendMessage', () => {
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
