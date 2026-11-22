import { act, renderHook, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { useSessionTimeout } from './useSessionTimeout';

type Reading = { secondsLeft: number; endsBy: 'idle' | 'limit' };

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'], shouldAdvanceTime: true });
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  server.resetHandlers();
});
afterAll(() => server.close());

function sessionRoute(respond: (read: number) => Response) {
  const state = { reads: 0 };
  server.use(
    http.get('*/api/session', () => {
      state.reads += 1;
      return respond(state.reads);
    }),
  );
  return state;
}

const reading = (value: Reading) => () => HttpResponse.json(value);
const problem = (status: number) => () => new HttpResponse(null, { status });

function watchTimers() {
  return vi.spyOn(globalThis, 'setTimeout');
}

async function scheduledIn(spy: ReturnType<typeof watchTimers>, delayMs: number) {
  await waitFor(() => expect(spy).toHaveBeenCalledWith(expect.any(Function), delayMs));
}

describe('useSessionTimeout', () => {
  it('shows no warning at 1800 s idle and reads again at 1680 s', async () => {
    const timers = watchTimers();
    const state = sessionRoute(reading({ secondsLeft: 1800, endsBy: 'idle' }));
    const { result } = renderHook(() => useSessionTimeout());

    await scheduledIn(timers, 1_680_000);
    expect(state.reads).toBe(1);
    await vi.advanceTimersByTimeAsync(1_679_000 - 1000);
    expect(state.reads).toBe(1);
    expect(result.current.warning).toBeNull();

    await vi.advanceTimersByTimeAsync(3000);
    await waitFor(() => expect(state.reads).toBe(2));
  });

  it('sets the warning at 120 s idle and reads again at 121 s', async () => {
    const timers = watchTimers();
    const state = sessionRoute((read) =>
      HttpResponse.json({ secondsLeft: read === 1 ? 120 : 0, endsBy: 'idle' }),
    );
    const { result } = renderHook(() => useSessionTimeout());

    await waitFor(() => expect(result.current.warning).toEqual({ secondsLeft: 120 }));
    await scheduledIn(timers, 121_000);
    expect(result.current.ended).toBe(false);
    await vi.advanceTimersByTimeAsync(120_000 - 1000);
    expect(state.reads).toBe(1);

    await vi.advanceTimersByTimeAsync(3000);
    await waitFor(() => expect(result.current.ended).toBe(true));
    expect(state.reads).toBe(2);
  });

  it('sets no warning when the 8 h limit is 120 s away', async () => {
    const timers = watchTimers();
    const state = sessionRoute(reading({ secondsLeft: 120, endsBy: 'limit' }));
    const { result } = renderHook(() => useSessionTimeout());

    await scheduledIn(timers, 121_000);
    expect(state.reads).toBe(1);
    expect(result.current.warning).toBeNull();
    expect(result.current.ended).toBe(false);
  });

  it('ends on a 401 and reads no more', async () => {
    const state = sessionRoute(problem(401));
    const { result } = renderHook(() => useSessionTimeout());

    await waitFor(() => expect(result.current.ended).toBe(true));
    await vi.advanceTimersByTimeAsync(3_600_000);
    expect(state.reads).toBe(1);
  });

  it('ends when no seconds are left and reads no more', async () => {
    const state = sessionRoute(reading({ secondsLeft: 0, endsBy: 'idle' }));
    const { result } = renderHook(() => useSessionTimeout());

    await waitFor(() => expect(result.current.ended).toBe(true));
    await vi.advanceTimersByTimeAsync(3_600_000);
    expect(state.reads).toBe(1);
  });

  it('does not end on a 500 and retries after 60 s', async () => {
    const timers = watchTimers();
    const state = sessionRoute((read) =>
      read === 1
        ? new HttpResponse(null, { status: 500 })
        : HttpResponse.json({ secondsLeft: 1800, endsBy: 'idle' }),
    );
    const { result } = renderHook(() => useSessionTimeout());

    await scheduledIn(timers, 60_000);
    await vi.advanceTimersByTimeAsync(59_000 - 1000);
    expect(state.reads).toBe(1);
    expect(result.current.ended).toBe(false);

    await vi.advanceTimersByTimeAsync(3000);
    await waitFor(() => expect(state.reads).toBe(2));
    expect(result.current.ended).toBe(false);
  });

  it('stay() calls /api/users/me, re-reads and clears the warning', async () => {
    const calls: string[] = [];
    sessionRoute((read) => {
      calls.push('session');
      return HttpResponse.json({ secondsLeft: read === 1 ? 120 : 1800, endsBy: 'idle' });
    });
    server.use(
      http.get('*/api/users/me', () => {
        calls.push('me');
        return HttpResponse.json({ id: 'u1', name: 'Ann', email: 'ann@example.com' });
      }),
    );
    const { result } = renderHook(() => useSessionTimeout());
    await waitFor(() => expect(result.current.warning).not.toBeNull());

    await act(async () => {
      await result.current.stay();
    });

    expect(calls).toEqual(['session', 'me', 'session']);
    expect(result.current.warning).toBeNull();
    expect(result.current.staying).toBe(false);
    expect(result.current.ended).toBe(false);
  });

  it('ends when stay() gets a 401', async () => {
    sessionRoute(reading({ secondsLeft: 120, endsBy: 'idle' }));
    server.use(http.get('*/api/users/me', problem(401)));
    const { result } = renderHook(() => useSessionTimeout());
    await waitFor(() => expect(result.current.warning).not.toBeNull());

    await act(async () => {
      await result.current.stay();
    });

    expect(result.current.ended).toBe(true);
  });

  it('ends when end() is called', async () => {
    const state = sessionRoute(reading({ secondsLeft: 1800, endsBy: 'idle' }));
    const { result } = renderHook(() => useSessionTimeout());
    await waitFor(() => expect(state.reads).toBe(1));

    act(() => result.current.end());

    expect(result.current.ended).toBe(true);
  });

  it('reads nothing after unmount', async () => {
    const timers = watchTimers();
    const state = sessionRoute(reading({ secondsLeft: 1800, endsBy: 'idle' }));
    const { unmount } = renderHook(() => useSessionTimeout());
    await scheduledIn(timers, 1_680_000);

    unmount();
    await vi.advanceTimersByTimeAsync(3_600_000);

    expect(state.reads).toBe(1);
  });
});
