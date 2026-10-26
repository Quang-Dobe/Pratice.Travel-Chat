import '@testing-library/jest-dom/vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw';
import { setupServer } from 'msw/node';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { ApiError, type Conversation, getConversation } from '../api/api';
import { ChatPage } from './ChatPage';

const encoder = new TextEncoder();

const frame = (event: string, data: unknown) =>
  encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);

function gatedStream(first: Uint8Array, rest: Uint8Array[]) {
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const body = new ReadableStream<Uint8Array>({
    async start(controller) {
      controller.enqueue(first);
      await gate;
      for (const chunk of rest) {
        controller.enqueue(chunk);
      }
      controller.close();
    },
  });
  return { body, release };
}

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
afterAll(() => server.close());

async function conversationLoader({ params }: { params: { id?: string } }) {
  try {
    return await getConversation(params.id ?? '');
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      return null;
    }
    throw error;
  }
}

function renderChat(initialPath: string) {
  const router = createMemoryRouter(
    [
      {
        path: '/',
        id: 'app',
        loader: () => ({}),
        children: [
          { index: true, Component: ChatPage },
          { path: 'c/:id', Component: ChatPage, loader: conversationLoader },
        ],
      },
      { path: '/login', element: <p>Login screen</p> },
    ],
    { initialEntries: [initialPath] },
  );
  render(<RouterProvider router={router} />);
  return router;
}

const savedConversation: Conversation = {
  id: 'c1',
  title: 'Hello',
  createdAt: '2026-10-01T00:00:00Z',
  messages: [
    { role: 'user', text: 'Hello' },
    { role: 'assistant', text: 'First part second part' },
  ],
};

describe('ChatPage', () => {
  it('shows partial reply text before the full text arrives and re-enables the input after done', async () => {
    const { body, release } = gatedStream(frame('token', { text: 'First part' }), [
      frame('token', { text: ' second part' }),
      frame('done', {}),
    ]);
    server.use(
      http.get('*/api/conversations/c1', () => HttpResponse.json(savedConversation)),
      http.post(
        '*/api/conversations/c1/messages',
        () => new HttpResponse(body, { headers: { 'Content-Type': 'text/event-stream' } }),
      ),
      http.get('*/api/conversations', () => HttpResponse.json({ items: [] })),
    );
    const router = renderChat('/c/c1');
    await screen.findByText('First part second part');

    await userEvent.type(screen.getByLabelText('Message'), 'Again');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));

    expect(await screen.findByText('First part')).toBeInTheDocument();
    expect(screen.getByLabelText('Message')).toBeDisabled();

    release();

    await waitFor(() => expect(screen.getByLabelText('Message')).toBeEnabled());
    expect(router.state.location.pathname).toBe('/c/c1');
  });

  it('creates a conversation on the first send, streams the reply and moves to /c/:id', async () => {
    const { body, release } = gatedStream(frame('token', { text: 'First part' }), [
      frame('token', { text: ' second part' }),
      frame('done', {}),
    ]);
    server.use(
      http.post('*/api/conversations', () =>
        HttpResponse.json(
          { id: 'c1', title: 'Hello', createdAt: '2026-10-01T00:00:00Z' },
          { status: 201 },
        ),
      ),
      http.post(
        '*/api/conversations/c1/messages',
        () => new HttpResponse(body, { headers: { 'Content-Type': 'text/event-stream' } }),
      ),
      http.get('*/api/conversations/c1', () => HttpResponse.json(savedConversation)),
      http.get('*/api/conversations', () => HttpResponse.json({ items: [] })),
    );
    const router = renderChat('/');

    await userEvent.type(await screen.findByLabelText('Message'), 'Hello');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));

    expect(await screen.findByText('First part')).toBeInTheDocument();
    expect(screen.getByText('Hello')).toBeInTheDocument();
    expect(screen.getByLabelText('Message')).toBeDisabled();

    release();

    await waitFor(() => expect(router.state.location.pathname).toBe('/c/c1'));
    await waitFor(() => expect(screen.getByText('First part second part')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByLabelText('Message')).toBeEnabled());
  });

  it('renders the loaded messages as plain text', async () => {
    server.use(
      http.get('*/api/conversations/c1', () =>
        HttpResponse.json({
          ...savedConversation,
          messages: [{ role: 'user', text: '<b>bold</b> **md**' }],
        }),
      ),
    );
    renderChat('/c/c1');

    expect(await screen.findByText('<b>bold</b> **md**')).toBeInTheDocument();
  });

  it('shows Conversation not found for an unknown id', async () => {
    server.use(http.get('*/api/conversations/nope', () => new HttpResponse(null, { status: 404 })));
    renderChat('/c/nope');

    expect(await screen.findByText('Conversation not found')).toBeInTheDocument();
  });

  it('shows an error and re-enables the input when sending fails', async () => {
    server.use(
      http.get('*/api/conversations/c1', () => HttpResponse.json(savedConversation)),
      http.post('*/api/conversations/c1/messages', () => new HttpResponse(null, { status: 500 })),
    );
    renderChat('/c/c1');
    await screen.findByText('First part second part');

    await userEvent.type(screen.getByLabelText('Message'), 'Again');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));

    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.getByLabelText('Message')).toBeEnabled();
    expect(screen.getByLabelText('Message')).toHaveValue('Again');
    expect(screen.getAllByText('Assistant')).toHaveLength(1);
    expect(screen.getAllByText('You')).toHaveLength(1);
  });

  it('restores the draft and adds no bubbles when creating the conversation fails', async () => {
    server.use(http.post('*/api/conversations', () => new HttpResponse(null, { status: 500 })));
    renderChat('/');

    await userEvent.type(await screen.findByLabelText('Message'), 'Hello');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));

    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.getByLabelText('Message')).toHaveValue('Hello');
    expect(screen.queryByText('Assistant')).not.toBeInTheDocument();
    expect(screen.queryByText('You')).not.toBeInTheDocument();
  });

  it('redirects to /login when sending returns 401', async () => {
    server.use(
      http.get('*/api/conversations/c1', () => HttpResponse.json(savedConversation)),
      http.post('*/api/conversations/c1/messages', () => new HttpResponse(null, { status: 401 })),
    );
    const router = renderChat('/c/c1');
    await screen.findByText('First part second part');

    await userEvent.type(screen.getByLabelText('Message'), 'Again');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => expect(router.state.location.pathname).toBe('/login'));
    expect(await screen.findByText('Login screen')).toBeInTheDocument();
  });
});
