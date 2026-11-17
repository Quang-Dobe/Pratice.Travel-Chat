import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw';
import { setupServer } from 'msw/node';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import type { AppData } from '../api/api';
import { Sidebar } from './Sidebar';

const data: AppData = {
  me: { id: 'u1', name: 'Ada Lovelace', email: 'ada@example.com' },
  conversations: [
    { id: 'c3', title: 'Newest trip', createdAt: '2026-10-03T00:00:00Z' },
    { id: 'c2', title: 'Middle trip', createdAt: '2026-10-02T00:00:00Z' },
    { id: 'c1', title: 'Oldest trip', createdAt: '2026-10-01T00:00:00Z' },
  ],
};

let logoutCalls = 0;

const server = setupServer(
  http.post('*/auth/logout', () => {
    logoutCalls += 1;
    return new HttpResponse(null, { status: 204 });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  logoutCalls = 0;
});
afterAll(() => server.close());

function renderSidebar() {
  const router = createMemoryRouter(
    [
      { path: '/', element: <Sidebar {...data} /> },
      { path: '/c/:id', element: <Sidebar {...data} /> },
      { path: '/login', element: <p>Login screen</p> },
    ],
    { initialEntries: ['/'] },
  );
  render(<RouterProvider router={router} />);
  return router;
}

describe('Sidebar', () => {
  it('renders new conversation, history, profile and logout', () => {
    renderSidebar();

    expect(screen.getByRole('link', { name: 'New conversation' })).toHaveAttribute('href', '/');
    expect(screen.getByRole('list', { name: 'Conversation history' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Profile' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Logout' })).toBeInTheDocument();
  });

  it('keeps the history in the given order and links each item to its conversation', () => {
    renderSidebar();

    const items = within(screen.getByRole('list', { name: 'Conversation history' })).getAllByRole(
      'link',
    );

    expect(items.map((item) => item.textContent)).toEqual([
      'Newest trip',
      'Middle trip',
      'Oldest trip',
    ]);
    expect(items.map((item) => item.getAttribute('href'))).toEqual(['/c/c3', '/c/c2', '/c/c1']);
  });

  it('posts to /auth/logout and lands on /login', async () => {
    const router = renderSidebar();

    await userEvent.click(screen.getByRole('button', { name: 'Logout' }));

    await waitFor(() => expect(router.state.location.pathname).toBe('/login'));
    expect(logoutCalls).toBe(1);
    expect(screen.getByText('Login screen')).toBeInTheDocument();
  });

  it('still lands on /login when logout fails', async () => {
    server.use(http.post('*/auth/logout', () => new HttpResponse(null, { status: 500 })));
    const router = renderSidebar();

    await userEvent.click(screen.getByRole('button', { name: 'Logout' }));

    await waitFor(() => expect(router.state.location.pathname).toBe('/login'));
  });

  it('shows name and email in the profile dialog', async () => {
    renderSidebar();

    await userEvent.click(screen.getByRole('button', { name: 'Profile' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Ada Lovelace')).toBeInTheDocument();
    expect(within(dialog).getByText('ada@example.com')).toBeInTheDocument();
  });
});
