import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw';
import { setupServer } from 'msw/node';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { ApiError, getConversation } from '../api/api';
import type { Conversation, Message, PlanCard } from '../api/schemas';
import { makeCard } from '../test/planCard';
import { ChatPage } from './ChatPage';

vi.mock('../components/PlanMap', () => ({
  PlanMap: ({ card }: { card: PlanCard }) =>
    card.reviewPins.length > 0 ? (
      <ul aria-label="Map points">
        {card.reviewPins.map((pin) => (
          <li key={`${pin.kind}-${pin.name}`}>
            {`${pin.kind === 'restaurant' ? 'Restaurant' : 'Place'}: ${pin.name}`}
          </li>
        ))}
      </ul>
    ) : null,
}));

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
    { role: 'user', text: 'Hello', toolCalls: [], planCard: null, pills: [] },
    { role: 'assistant', text: 'First part second part', toolCalls: [], planCard: null, pills: [] },
  ],
};

const eventStream = (frames: Uint8Array[]) =>
  new HttpResponse(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of frames) {
          controller.enqueue(chunk);
        }
        controller.close();
      },
    }),
    { headers: { 'Content-Type': 'text/event-stream' } },
  );

const approvalPills = [
  { text: 'Approve', action: 'approve' },
  { text: 'Reject', action: 'reject' },
] as const;

function withCard(card: PlanCard, pills: Message['pills'] = [...approvalPills]): Conversation {
  return {
    ...savedConversation,
    messages: [
      savedConversation.messages[0] as Message,
      { role: 'assistant', text: 'Here is your plan', toolCalls: [], planCard: card, pills },
    ],
  };
}

function noApprovalsRoute() {
  const calls: string[] = [];
  server.use(
    http.post('*/api/conversations/c1/approvals', ({ request }) => {
      calls.push(request.url);
      return new HttpResponse(null, { status: 404 });
    }),
  );
  return calls;
}

describe('ChatPage plan card', () => {
  it('posts the Approve pill with the card id and the current picks', async () => {
    let body: unknown;
    let loads = 0;
    const approvals = noApprovalsRoute();
    server.use(
      http.get('*/api/conversations/c1', () => {
        loads += 1;
        return HttpResponse.json(
          loads === 1
            ? withCard(makeCard())
            : withCard(
                makeCard({
                  status: 'approved',
                  picked: { flightId: 'f1', hotelId: 'h2' },
                  budget: {
                    flightUsd: 500,
                    hotelUsd: 700,
                    dailyUsd: 90,
                    totalUsd: 1290,
                    budgetUsd: 2000,
                    overBudget: false,
                  },
                }),
                [],
              ),
        );
      }),
      http.post('*/api/conversations/c1/messages', async ({ request }) => {
        body = await request.json();
        return eventStream([frame('token', { text: 'Trip confirmed' }), frame('done', {})]);
      }),
    );
    renderChat('/c/c1');

    await userEvent.click(await screen.findByRole('radio', { name: 'Hotel two' }));
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }));

    expect(await screen.findByText('Trip confirmed')).toBeInTheDocument();
    expect(body).toEqual({
      text: 'Approve',
      action: { type: 'approve', cardId: 'card1', flightId: 'f1', hotelId: 'h2' },
    });
    const card = screen.getByRole('group', { name: 'Plan card' });
    await waitFor(() => expect(within(card).getByText('Approved')).toBeInTheDocument());
    expect(within(card).getByRole('radio', { name: 'Hotel two' })).toBeChecked();
    expect(within(card).getByRole('radio', { name: 'Hotel two' })).toBeDisabled();
    expect(within(card).getByText('Total $1,290.00 of $2,000.00 budget')).toBeInTheDocument();
    expect(approvals).toEqual([]);
  });

  it('posts the Reject pill with its action and the card id', async () => {
    let body: unknown;
    server.use(
      http.get('*/api/conversations/c1', () => HttpResponse.json(withCard(makeCard()))),
      http.post('*/api/conversations/c1/messages', async ({ request }) => {
        body = await request.json();
        return eventStream([frame('token', { text: 'What should change?' }), frame('done', {})]);
      }),
    );
    renderChat('/c/c1');

    await userEvent.click(await screen.findByRole('button', { name: 'Reject' }));

    expect(await screen.findByText('What should change?')).toBeInTheDocument();
    expect(body).toEqual({ text: 'Reject', action: { type: 'reject', cardId: 'card1' } });
  });

  it('sends a plain pill without an action', async () => {
    let body: unknown;
    server.use(
      http.get('*/api/conversations/c1', () =>
        HttpResponse.json(withCard(makeCard(), [{ text: 'Add a hotel', action: null }])),
      ),
      http.post('*/api/conversations/c1/messages', async ({ request }) => {
        body = await request.json();
        return eventStream([frame('token', { text: 'Sure thing' }), frame('done', {})]);
      }),
    );
    renderChat('/c/c1');

    await userEvent.click(await screen.findByRole('button', { name: 'Add a hotel' }));

    expect(await screen.findByText('Sure thing')).toBeInTheDocument();
    expect(body).toEqual({ text: 'Add a hotel', action: null });
  });

  it('makes a loaded open card interactive again and keeps a replaced one read-only', async () => {
    server.use(
      http.get('*/api/conversations/c1', () =>
        HttpResponse.json({
          ...savedConversation,
          messages: [
            { role: 'user', text: 'Plan it', toolCalls: [], planCard: null, pills: [] },
            {
              role: 'assistant',
              text: 'First plan',
              toolCalls: [],
              planCard: makeCard({ id: 'old', status: 'replaced' }),
              pills: [],
            },
            { role: 'user', text: 'Cheaper hotel', toolCalls: [], planCard: null, pills: [] },
            {
              role: 'assistant',
              text: 'Second plan',
              toolCalls: [],
              planCard: makeCard({ id: 'new' }),
              pills: [...approvalPills],
            },
          ],
        }),
      ),
    );
    renderChat('/c/c1');

    const cards = await screen.findAllByRole('group', { name: 'Plan card' });
    const [oldCard, newCard] = cards;
    expect(oldCard).toBeDefined();
    expect(newCard).toBeDefined();
    expect(within(oldCard as HTMLElement).getByText('Replaced')).toBeInTheDocument();
    expect(within(oldCard as HTMLElement).getByRole('radio', { name: 'Hotel two' })).toBeDisabled();
    expect(within(newCard as HTMLElement).getByRole('radio', { name: 'Hotel two' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument();
  });

  it('shows the over-budget warning from a loaded card', async () => {
    server.use(
      http.get('*/api/conversations/c1', () =>
        HttpResponse.json(
          withCard(
            makeCard({
              budget: {
                flightUsd: 500,
                hotelUsd: 700,
                dailyUsd: 90,
                totalUsd: 1290,
                budgetUsd: 600,
                overBudget: true,
              },
              picked: { flightId: 'f1', hotelId: 'h2' },
              status: 'approved',
            }),
            [],
          ),
        ),
      ),
    );
    renderChat('/c/c1');

    const card = await screen.findByRole('group', { name: 'Plan card' });
    expect(within(card).getByText('This plan is over budget.')).toBeInTheDocument();
    expect(within(card).getByText('Total $1,290.00 of $600.00 budget')).toBeInTheDocument();
  });

  it('applies map_pins to the card with that id and ignores other ids', async () => {
    const { body, release } = gatedStream(
      Uint8Array.from([
        ...frame('map_pins', {
          cardId: 'card1',
          pins: [{ name: 'Ramen Bar', kind: 'restaurant', lat: 35.68, lon: 139.76 }],
        }),
        ...frame('map_pins', {
          cardId: 'other',
          pins: [{ name: 'Ghost Cafe', kind: 'place', lat: 1, lon: 1 }],
        }),
      ]),
      [frame('token', { text: 'More pins' }), frame('done', {})],
    );
    server.use(
      http.get('*/api/conversations/c1', () => HttpResponse.json(withCard(makeCard()))),
      http.post(
        '*/api/conversations/c1/messages',
        () => new HttpResponse(body, { headers: { 'Content-Type': 'text/event-stream' } }),
      ),
      http.get('*/api/conversations', () => HttpResponse.json({ items: [] })),
    );
    renderChat('/c/c1');
    await screen.findByRole('group', { name: 'Plan card' });
    expect(screen.queryByRole('list', { name: 'Map points' })).not.toBeInTheDocument();

    await userEvent.type(screen.getByLabelText('Message'), 'Show restaurants');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));

    const points = await screen.findByRole('list', { name: 'Map points' });
    expect(within(points).getAllByRole('listitem')).toHaveLength(1);
    expect(within(points).getByText('Restaurant: Ramen Bar')).toBeInTheDocument();
    expect(screen.queryByText('Place: Ghost Cafe')).not.toBeInTheDocument();

    release();
    await waitFor(() => expect(screen.getByLabelText('Message')).toBeEnabled());
  });

  it('draws the map only on the latest card', async () => {
    const withPin = (id: string) =>
      makeCard({
        id,
        reviewPins: [{ name: `Pin ${id}`, kind: 'place', lat: 1, lon: 1 }],
      });
    server.use(
      http.get('*/api/conversations/c1', () =>
        HttpResponse.json({
          ...savedConversation,
          messages: [
            { role: 'user', text: 'Plan it', toolCalls: [], planCard: null, pills: [] },
            {
              role: 'assistant',
              text: 'First plan',
              toolCalls: [],
              planCard: { ...withPin('old'), status: 'replaced' },
              pills: [],
            },
            {
              role: 'assistant',
              text: 'Second plan',
              toolCalls: [],
              planCard: withPin('new'),
              pills: [],
            },
          ],
        }),
      ),
    );
    renderChat('/c/c1');

    const lists = await screen.findAllByRole('list', { name: 'Map points' });
    expect(lists).toHaveLength(1);
    expect(within(lists[0] as HTMLElement).getByText('Place: Pin new')).toBeInTheDocument();
  });

  it('renders a streamed plan card, then marks the earlier card replaced after done', async () => {
    let loads = 0;
    const earlier = makeCard({ id: 'old' });
    const next = makeCard({ id: 'new' });
    server.use(
      http.get('*/api/conversations/c1', () => {
        loads += 1;
        return HttpResponse.json({
          ...savedConversation,
          messages: [
            { role: 'user', text: 'Plan it', toolCalls: [], planCard: null, pills: [] },
            {
              role: 'assistant',
              text: 'First plan',
              toolCalls: [],
              planCard: loads === 1 ? earlier : { ...earlier, status: 'replaced' },
              pills: loads === 1 ? [...approvalPills] : [],
            },
            ...(loads === 1
              ? []
              : [
                  { role: 'user', text: 'Cheaper hotel', toolCalls: [], planCard: null, pills: [] },
                  {
                    role: 'assistant',
                    text: 'Second plan',
                    toolCalls: [],
                    planCard: next,
                    pills: [...approvalPills],
                  },
                ]),
          ],
        });
      }),
      http.post('*/api/conversations/c1/messages', () =>
        eventStream([
          frame('token', { text: 'Second plan' }),
          frame('plan_card', next),
          frame('pills', { items: approvalPills }),
          frame('done', {}),
        ]),
      ),
    );
    renderChat('/c/c1');
    await screen.findByText('First plan');

    await userEvent.type(screen.getByLabelText('Message'), 'Cheaper hotel');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));

    expect(await screen.findByText('Second plan')).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getAllByRole('group', { name: 'Plan card' })).toHaveLength(2),
    );
    const [oldCard, newCard] = screen.getAllByRole('group', { name: 'Plan card' });
    await waitFor(() =>
      expect(within(oldCard as HTMLElement).getByText('Replaced')).toBeInTheDocument(),
    );
    expect(within(newCard as HTMLElement).queryByText('Replaced')).not.toBeInTheDocument();
    await waitFor(() =>
      expect(
        within(newCard as HTMLElement).getByRole('radio', { name: 'Hotel two' }),
      ).toBeEnabled(),
    );
    expect(screen.getByRole('button', { name: 'Approve' })).toBeEnabled();
  });
});

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

  it('shows the tool chip while no token has arrived', async () => {
    const { body, release } = gatedStream(
      frame('tool_call', { kind: 'retrieve', name: 'flights__search', status: 'started' }),
      [
        frame('tool_call', { kind: 'retrieve', name: 'flights__search', status: 'finished' }),
        frame('token', { text: 'Found flights' }),
        frame('done', {}),
      ],
    );
    server.use(
      http.get('*/api/conversations/c1', () => HttpResponse.json(savedConversation)),
      http.post(
        '*/api/conversations/c1/messages',
        () => new HttpResponse(body, { headers: { 'Content-Type': 'text/event-stream' } }),
      ),
      http.get('*/api/conversations', () => HttpResponse.json({ items: [] })),
    );
    renderChat('/c/c1');
    await screen.findByText('First part second part');

    await userEvent.type(screen.getByLabelText('Message'), 'Find flights');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));

    const progress = await screen.findByRole('group', { name: 'Tool progress' });
    expect(within(progress).getByText('flights__search')).toBeInTheDocument();
    expect(screen.getByLabelText('Assistant is typing')).toBeInTheDocument();

    release();

    expect(await screen.findByText('Found flights')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText('Message')).toBeEnabled());
    expect(screen.getAllByText('flights__search')).toHaveLength(1);
  });

  it('sends a clicked pill as the next user message and streams a reply', async () => {
    server.use(
      http.get('*/api/conversations/c1', () =>
        HttpResponse.json({
          ...savedConversation,
          messages: [
            savedConversation.messages[0],
            {
              ...savedConversation.messages[1],
              pills: [{ text: 'Add a hotel', action: null }],
            },
          ],
        }),
      ),
      http.post('*/api/conversations/c1/messages', () =>
        eventStream([frame('token', { text: 'Sure thing' }), frame('done', {})]),
      ),
      http.get('*/api/conversations', () => HttpResponse.json({ items: [] })),
    );
    renderChat('/c/c1');

    await userEvent.click(await screen.findByRole('button', { name: 'Add a hotel' }));

    expect(await screen.findByText('Sure thing')).toBeInTheDocument();
    expect(screen.getByText('Add a hotel')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add a hotel' })).not.toBeInTheDocument();
    expect(screen.getAllByText('You')).toHaveLength(2);
  });

  it('shows a notice as an alert in the assistant bubble', async () => {
    server.use(
      http.get('*/api/conversations/c1', () => HttpResponse.json(savedConversation)),
      http.post('*/api/conversations/c1/messages', () =>
        eventStream([
          frame('notice', { message: 'The AI model is not configured.' }),
          frame('done', {}),
        ]),
      ),
      http.get('*/api/conversations', () => HttpResponse.json({ items: [] })),
    );
    renderChat('/c/c1');
    await screen.findByText('First part second part');

    await userEvent.type(screen.getByLabelText('Message'), 'Again');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('The AI model is not configured.');
    await waitFor(() => expect(screen.getByLabelText('Message')).toBeEnabled());
    expect(screen.queryByLabelText('Assistant is typing')).not.toBeInTheDocument();
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
