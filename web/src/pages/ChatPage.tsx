import { Alert, Box, Stack, Typography } from '@mui/material';
import { useEffect, useRef, useState } from 'react';
import { useLoaderData, useNavigate, useRevalidator, useRouteLoaderData } from 'react-router';
import {
  type Action,
  type AppData,
  type Conversation,
  createConversation,
  getConversation,
  type Picked,
  type Pill,
  type PlanCard as PlanCardData,
  type StreamEvent,
  sendMessage,
  UnauthorizedError,
} from '../api/api';
import { Composer } from '../components/Composer';
import { EmptyState } from '../components/EmptyState';
import { initials } from '../components/initials';
import { MessageBubble } from '../components/MessageBubble';
import {
  applyEvent,
  applyMapPins,
  type ChatMessage,
  latestCardIndex,
  newMessage,
} from './chatMessages';

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}

function ChatView({ conversation }: { conversation: Conversation | undefined }) {
  const navigate = useNavigate();
  const revalidator = useRevalidator();
  const app = useRouteLoaderData<AppData | undefined>('app');
  const [conversationId, setConversationId] = useState(conversation?.id);
  const [messages, setMessages] = useState<ChatMessage[]>(conversation?.messages ?? []);
  const [draft, setDraft] = useState('');
  const [picks, setPicks] = useState<Record<string, Picked>>({});
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  useEffect(() => {
    const scroller = scrollRef.current;
    if (scroller && messages.length > 0) {
      scroller.scrollTop = scroller.scrollHeight;
    }
  }, [messages]);

  function applyToLast(event: StreamEvent) {
    setMessages((previous) =>
      previous.map((message, index) =>
        index === previous.length - 1 ? applyEvent(message, event) : message,
      ),
    );
  }

  function pickedFor(card: PlanCardData): Picked {
    return card.status === 'open' ? (picks[card.id] ?? card.picked) : card.picked;
  }

  async function send(text: string, action?: Action) {
    const controller = new AbortController();
    abortRef.current = controller;
    setError(null);
    setStreaming(true);
    const isNew = conversationId === undefined;
    const before = messages;
    let id = conversationId;
    let appended = false;
    try {
      if (id === undefined) {
        id = (await createConversation(text)).id;
        setConversationId(id);
      }
      setMessages((previous) => [...previous, newMessage('user', text), newMessage('assistant')]);
      appended = true;
      for await (const event of sendMessage(id, text, action, controller.signal)) {
        if (event.type === 'map_pins') {
          setMessages((previous) => applyMapPins(previous, event.cardId, event.pins));
        } else {
          applyToLast(event);
        }
      }
    } catch (caught) {
      if (appended) {
        setMessages(before);
      }
      setDraft(text);
      if (caught instanceof UnauthorizedError) {
        await navigate('/login');
      } else if (!isAbort(caught)) {
        setError('The message could not be sent. Please try again.');
        if (id !== undefined) {
          await revalidator.revalidate();
        }
      }
      setStreaming(false);
      return;
    }
    if (id !== undefined) {
      await syncCards(id);
    }
    setStreaming(false);
    try {
      if (isNew) {
        await navigate(`/c/${id}`, { replace: true });
      }
      await revalidator.revalidate();
    } catch {
      setError('The conversation list could not be refreshed.');
    }
  }

  async function syncCards(id: string) {
    try {
      const fresh = await getConversation(id);
      const byId = new Map<string, PlanCardData>();
      for (const message of fresh.messages) {
        if (message.planCard !== null) {
          byId.set(message.planCard.id, message.planCard);
        }
      }
      setMessages((previous) =>
        previous.map((message) => {
          const server = message.planCard === null ? undefined : byId.get(message.planCard.id);
          return message.planCard !== null && server !== undefined
            ? { ...message, planCard: server }
            : message;
        }),
      );
    } catch {
      return;
    }
  }

  function submit(text: string, action?: Action) {
    if (!text || streaming) {
      return;
    }
    setDraft('');
    void send(text, action);
  }

  function pickPill(pill: Pill) {
    const card = messages[latestCardIndex(messages)]?.planCard ?? null;
    if (card && pill.action === 'approve') {
      const { flightId, hotelId } = pickedFor(card);
      submit(pill.text, { type: 'approve', cardId: card.id, flightId, hotelId });
    } else if (card && pill.action === 'reject') {
      submit(pill.text, { type: 'reject', cardId: card.id });
    } else {
      submit(pill.text);
    }
  }

  function pickOptions(cardId: string, next: Picked) {
    setPicks((previous) => ({ ...previous, [cardId]: next }));
  }

  const isEmpty = messages.length === 0;
  const latestIndex = latestCardIndex(messages);
  const userInitials = initials(app?.me?.name ?? '');

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      {isEmpty && <EmptyState name={app?.me?.name} disabled={streaming} onPick={submit} />}
      <Box
        ref={scrollRef}
        sx={{
          flexGrow: isEmpty ? 0 : 1,
          overflowY: 'auto',
          px: { xs: 2, md: 4 },
          py: isEmpty ? 0 : 3,
        }}
      >
        <Stack
          role="log"
          aria-label="Conversation"
          spacing={2.5}
          sx={{ maxWidth: 820, mx: 'auto' }}
        >
          {messages.map((message, index) => (
            <MessageBubble
              // biome-ignore lint/suspicious/noArrayIndexKey: messages are append-only with no stable id
              key={index}
              message={message}
              userInitials={userInitials}
              isLast={index === messages.length - 1}
              streaming={streaming}
              cardInteractive={
                index === latestIndex && message.planCard?.status === 'open' && !streaming
              }
              showMap={index === latestIndex}
              picked={message.planCard ? pickedFor(message.planCard) : null}
              onPickPill={pickPill}
              onPickOptions={pickOptions}
            />
          ))}
        </Stack>
      </Box>
      <Box sx={{ px: { xs: 2, md: 4 }, pb: 2.5, pt: 1 }}>
        <Box sx={{ maxWidth: 820, mx: 'auto' }}>
          {error && (
            <Alert severity="error" sx={{ mb: 1.5 }}>
              {error}
            </Alert>
          )}
          <Composer
            value={draft}
            disabled={streaming}
            onChange={setDraft}
            onSend={() => submit(draft.trim())}
          />
          <Typography
            variant="caption"
            color="text.secondary"
            component="div"
            sx={{ textAlign: 'center', mt: 1 }}
          >
            Enter to send · Shift + Enter for a new line
          </Typography>
        </Box>
      </Box>
    </Box>
  );
}

export function ChatPage() {
  const conversation = useLoaderData<Conversation | null | undefined>();

  if (conversation === null) {
    return (
      <Box
        sx={{ height: '100vh', display: 'grid', placeItems: 'center', p: 3, textAlign: 'center' }}
      >
        <Box>
          <Typography variant="h5" component="h1">
            Conversation not found
          </Typography>
          <Typography color="text.secondary" sx={{ mt: 1 }}>
            It may have been removed, or it belongs to another account.
          </Typography>
        </Box>
      </Box>
    );
  }

  return <ChatView key={conversation?.id ?? 'new'} conversation={conversation} />;
}
