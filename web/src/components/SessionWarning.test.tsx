import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { useSessionTimeout } from '../pages/useSessionTimeout';
import { SessionWarning } from './SessionWarning';

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function Harness() {
  const { warning, staying, stay } = useSessionTimeout();
  return warning ? (
    <SessionWarning secondsLeft={warning.secondsLeft} busy={staying} onStay={stay} />
  ) : null;
}

describe('SessionWarning', () => {
  it('reads the minutes left, rounded up', () => {
    render(<SessionWarning secondsLeft={120} busy={false} onStay={vi.fn()} />);

    expect(screen.getByRole('alert')).toHaveTextContent('Your session ends in 2 minutes');
  });

  it('reads "in less than a minute" under 60 s', () => {
    render(<SessionWarning secondsLeft={45} busy={false} onStay={vi.fn()} />);

    expect(screen.getByRole('alert')).toHaveTextContent('Your session ends in less than a minute');
  });

  it('disables Stay signed in while busy', () => {
    render(<SessionWarning secondsLeft={120} busy onStay={vi.fn()} />);

    expect(screen.getByRole('button', { name: 'Stay signed in' })).toBeDisabled();
  });

  it('shows at 120 s left and sends GET /api/users/me on Stay signed in', async () => {
    let meCalls = 0;
    let sessionReads = 0;
    server.use(
      http.get('*/api/session', () => {
        sessionReads += 1;
        return HttpResponse.json({ secondsLeft: 120, endsBy: 'idle' });
      }),
      http.get('*/api/users/me', () => {
        meCalls += 1;
        return HttpResponse.json({ id: 'u1', name: 'Ann', email: 'ann@example.com' });
      }),
    );
    render(<Harness />);

    expect(await screen.findByRole('alert')).toHaveTextContent('Your session ends in 2 minutes');
    await userEvent.click(screen.getByRole('button', { name: 'Stay signed in' }));

    await waitFor(() => expect(meCalls).toBe(1));
    await waitFor(() => expect(sessionReads).toBe(2));
  });
});
