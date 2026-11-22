import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw';
import { setupServer } from 'msw/node';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import type { AppData } from '../api/schemas';
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
let loaderCalls = 0;

const server = setupServer(
  http.post('*/auth/logout', () => {
    logoutCalls += 1;
    return new HttpResponse(null, { status: 204 });
  }),
  http.get('*/api/conversations/starters', () => HttpResponse.json({ enabled: true, items: [] })),
);

const SWITCH_LABEL = 'Suggestions from my past trips';

async function openProfile() {
  await userEvent.click(screen.getByRole('button', { name: 'Profile' }));
  const dialog = await screen.findByRole('dialog');
  const toggle = within(dialog).getByLabelText(SWITCH_LABEL);
  await waitFor(() => expect(toggle).toBeEnabled());
  return { dialog, toggle };
}

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  logoutCalls = 0;
  loaderCalls = 0;
});
afterAll(() => server.close());

function renderSidebar() {
  const router = createMemoryRouter(
    [
      {
        id: 'app',
        path: '/',
        element: <Sidebar {...data} />,
        loader: () => {
          loaderCalls += 1;
          return null;
        },
      },
      { path: '/c/:id', element: <Sidebar {...data} /> },
      { path: '/login', element: <p>Login screen</p> },
    ],
    { initialEntries: ['/'], hydrationData: { loaderData: { app: null } } },
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

  it('shows the suggestions switch on by default', async () => {
    renderSidebar();

    const { toggle } = await openProfile();

    expect(toggle).toBeChecked();
  });

  it('sends PUT enabled false when toggled and shows the switch off', async () => {
    let body: unknown;
    server.use(
      http.put('*/api/conversations/starters/setting', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ enabled: false });
      }),
    );
    renderSidebar();
    const { toggle } = await openProfile();

    await userEvent.click(toggle);

    await waitFor(() => expect(toggle).not.toBeChecked());
    await waitFor(() => expect(toggle).toBeEnabled());
    expect(body).toEqual({ enabled: false });
    await waitFor(() => expect(loaderCalls).toBe(1));
  });

  it('shows the error and keeps the value when the PUT fails', async () => {
    server.use(
      http.put(
        '*/api/conversations/starters/setting',
        () => new HttpResponse(null, { status: 500 }),
      ),
    );
    renderSidebar();
    const { dialog, toggle } = await openProfile();

    await userEvent.click(toggle);

    expect(await within(dialog).findByRole('alert')).toBeInTheDocument();
    await waitFor(() => expect(toggle).toBeEnabled());
    expect(toggle).toBeChecked();
    expect(loaderCalls).toBe(0);
  });

  it('sends DELETE on clear and shows the note', async () => {
    let deletes = 0;
    server.use(
      http.delete('*/api/conversations/starters/history', () => {
        deletes += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderSidebar();
    const { dialog } = await openProfile();

    await userEvent.click(within(dialog).getByRole('button', { name: 'Clear suggestion history' }));

    expect(await within(dialog).findByText('Suggestion history cleared.')).toBeInTheDocument();
    expect(deletes).toBe(1);
    await waitFor(() => expect(loaderCalls).toBe(1));
  });

  it('shows the error when the DELETE fails', async () => {
    server.use(
      http.delete(
        '*/api/conversations/starters/history',
        () => new HttpResponse(null, { status: 500 }),
      ),
    );
    renderSidebar();
    const { dialog } = await openProfile();

    await userEvent.click(within(dialog).getByRole('button', { name: 'Clear suggestion history' }));

    expect(await within(dialog).findByRole('alert')).toBeInTheDocument();
    expect(within(dialog).queryByText('Suggestion history cleared.')).not.toBeInTheDocument();
    await waitFor(() =>
      expect(
        within(dialog).getByRole('button', { name: 'Clear suggestion history' }),
      ).toBeEnabled(),
    );
    expect(loaderCalls).toBe(0);
  });
});
