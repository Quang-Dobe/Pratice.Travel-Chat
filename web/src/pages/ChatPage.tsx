import ArrowUpwardRounded from '@mui/icons-material/ArrowUpwardRounded';
import AutoAwesomeRounded from '@mui/icons-material/AutoAwesomeRounded';
import BeachAccessRounded from '@mui/icons-material/BeachAccessRounded';
import LandscapeRounded from '@mui/icons-material/LandscapeRounded';
import RamenDiningRounded from '@mui/icons-material/RamenDiningRounded';
import SavingsRounded from '@mui/icons-material/SavingsRounded';
import TravelExploreRounded from '@mui/icons-material/TravelExploreRounded';
import {
  Alert,
  Avatar,
  Box,
  Card,
  CardActionArea,
  IconButton,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import { keyframes } from '@mui/material/styles';
import { type FormEvent, type ReactNode, useEffect, useRef, useState } from 'react';
import { useLoaderData, useNavigate, useRevalidator, useRouteLoaderData } from 'react-router';
import {
  type Action,
  type AppData,
  type Conversation,
  createConversation,
  getConversation,
  type Message,
  type Picked,
  type Pill,
  type PlanCard as PlanCardData,
  type StreamEvent,
  sendMessage,
  UnauthorizedError,
} from '../api/api';
import { initials } from '../components/initials';
import { PlanCard } from '../components/PlanCard';
import { SuggestionPills } from '../components/SuggestionPills';
import { ToolProgress } from '../components/ToolProgress';
import { brandGradient } from '../theme';

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}

const blink = keyframes`
  0%, 80%, 100% { opacity: 0.25; transform: translateY(0); }
  40% { opacity: 1; transform: translateY(-2px); }
`;

function TypingDots() {
  return (
    <Box
      component="span"
      aria-label="Assistant is typing"
      sx={{ display: 'inline-flex', gap: 0.5, py: 0.75 }}
    >
      {[0, 1, 2].map((dot) => (
        <Box
          key={dot}
          component="span"
          sx={{
            width: 6,
            height: 6,
            borderRadius: '50%',
            bgcolor: 'text.secondary',
            animation: `${blink} 1.2s ${dot * 0.15}s infinite ease-in-out`,
          }}
        />
      ))}
    </Box>
  );
}

type ChatMessage = Message & { notice?: string };

function emptyAssistant(): ChatMessage {
  return { role: 'assistant', text: '', toolCalls: [], planCard: null, pills: [] };
}

function latestCardIndex(messages: ChatMessage[]): number {
  return messages.findLastIndex((message) => message.planCard !== null);
}

function applyEvent(message: ChatMessage, event: StreamEvent): ChatMessage {
  switch (event.type) {
    case 'token':
      return { ...message, text: message.text + event.text };
    case 'tool_call': {
      const { kind, name, status } = event;
      if (status === 'started') {
        return { ...message, toolCalls: [...message.toolCalls, { kind, name, status }] };
      }
      const openIndex = message.toolCalls.findLastIndex(
        (call) => call.name === name && call.status === 'started',
      );
      if (openIndex === -1) {
        return { ...message, toolCalls: [...message.toolCalls, { kind, name, status }] };
      }
      return {
        ...message,
        toolCalls: message.toolCalls.map((call, index) =>
          index === openIndex ? { ...call, status } : call,
        ),
      };
    }
    case 'plan_card':
      return { ...message, planCard: event.card };
    case 'pills':
      return { ...message, pills: event.items };
    case 'notice':
      return { ...message, notice: event.message };
    case 'done':
      return message;
  }
}

type MessageBubbleProps = {
  message: ChatMessage;
  userInitials: string;
  isLast: boolean;
  streaming: boolean;
  cardInteractive: boolean;
  picked: Picked | null;
  onPickPill: (pill: Pill) => void;
  onPickOptions: (cardId: string, picked: Picked) => void;
};

function MessageBubble({
  message,
  userInitials,
  isLast,
  streaming,
  cardInteractive,
  picked,
  onPickPill,
  onPickOptions,
}: MessageBubbleProps) {
  const isUser = message.role === 'user';
  const showDots = !isUser && message.text === '' && !message.notice && streaming && isLast;
  const { planCard } = message;
  return (
    <Box
      sx={{
        display: 'flex',
        gap: 1.5,
        alignItems: 'flex-start',
        flexDirection: isUser ? 'row-reverse' : 'row',
      }}
    >
      <Avatar
        sx={{
          width: 32,
          height: 32,
          fontSize: 14,
          bgcolor: isUser ? 'primary.main' : 'secondary.main',
        }}
      >
        {isUser ? userInitials : <TravelExploreRounded fontSize="small" />}
      </Avatar>
      <Paper
        elevation={0}
        sx={{
          px: 2,
          py: 1.25,
          maxWidth: '75%',
          ...(planCard ? { width: '75%' } : {}),
          borderRadius: 3,
          ...(isUser
            ? { bgcolor: 'primary.main', color: 'primary.contrastText', borderTopRightRadius: 4 }
            : { border: 1, borderColor: 'divider', borderTopLeftRadius: 4 }),
        }}
      >
        <Typography variant="caption" component="p" sx={{ fontWeight: 600, opacity: 0.75 }}>
          {isUser ? 'You' : 'Assistant'}
        </Typography>
        {!isUser && <ToolProgress toolCalls={message.toolCalls} />}
        <Typography component="p" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
          {showDots ? <TypingDots /> : message.text}
        </Typography>
        {message.notice && (
          <Alert severity="info" sx={{ mt: 1 }}>
            {message.notice}
          </Alert>
        )}
        {planCard && picked && (
          <PlanCard
            card={planCard}
            picked={picked}
            interactive={cardInteractive}
            onPick={(next) => onPickOptions(planCard.id, next)}
          />
        )}
        {!isUser && isLast && (
          <SuggestionPills items={message.pills} disabled={streaming} onPick={onPickPill} />
        )}
      </Paper>
    </Box>
  );
}

type Starter = { icon: ReactNode; title: string; prompt: string };

const starters: Starter[] = [
  {
    icon: <RamenDiningRounded />,
    title: 'Food and hiking in Tokyo',
    prompt: '5 days in Tokyo, mid-budget, food and hiking',
  },
  {
    icon: <BeachAccessRounded />,
    title: 'A long weekend by the sea',
    prompt: 'A relaxed 3-day beach weekend in Da Nang for two',
  },
  {
    icon: <SavingsRounded />,
    title: 'Europe on a budget',
    prompt: 'One week in Lisbon and Porto on a tight budget',
  },
  {
    icon: <LandscapeRounded />,
    title: 'Best time to go',
    prompt: 'When is the best weather to visit Iceland?',
  },
];

type EmptyStateProps = {
  name: string | undefined;
  disabled: boolean;
  onPick: (prompt: string) => void;
};

function EmptyState({ name, disabled, onPick }: EmptyStateProps) {
  const firstName = name?.trim().split(/\s+/)[0];
  return (
    <Box
      sx={{ flexGrow: 1, display: 'grid', placeItems: 'center', px: 3, py: 4, overflowY: 'auto' }}
    >
      <Box sx={{ width: '100%', maxWidth: 720, textAlign: 'center' }}>
        <Box
          sx={{
            mx: 'auto',
            mb: 2,
            width: 56,
            height: 56,
            borderRadius: 4,
            display: 'grid',
            placeItems: 'center',
            color: '#fff',
            background: brandGradient,
          }}
        >
          <AutoAwesomeRounded />
        </Box>
        <Typography variant="h4" component="h1">
          {firstName ? `Where to next, ${firstName}?` : 'Where to next?'}
        </Typography>
        <Typography color="text.secondary" sx={{ mt: 1, mb: 4 }}>
          Tell me where, when and what you love, and I will help plan the trip.
        </Typography>
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 1.5 }}>
          {starters.map((starter) => (
            <Card
              key={starter.title}
              variant="outlined"
              sx={{ borderRadius: 3, textAlign: 'left' }}
            >
              <CardActionArea
                disabled={disabled}
                onClick={() => onPick(starter.prompt)}
                sx={{ p: 2, height: '100%', display: 'flex', alignItems: 'flex-start', gap: 1.5 }}
              >
                <Box sx={{ color: 'primary.main', display: 'flex', pt: 0.25 }}>{starter.icon}</Box>
                <Box sx={{ flexGrow: 1 }}>
                  <Typography variant="subtitle2">{starter.title}</Typography>
                  <Typography variant="body2" color="text.secondary">
                    {starter.prompt}
                  </Typography>
                </Box>
              </CardActionArea>
            </Card>
          ))}
        </Box>
      </Box>
    </Box>
  );
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
      setMessages((previous) => [
        ...previous,
        { role: 'user', text, toolCalls: [], planCard: null, pills: [] },
        emptyAssistant(),
      ]);
      appended = true;
      for await (const event of sendMessage(id, text, action, controller.signal)) {
        applyToLast(event);
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

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    submit(draft.trim());
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
          <Paper
            component="form"
            onSubmit={handleSubmit}
            elevation={0}
            sx={{
              display: 'flex',
              alignItems: 'flex-end',
              gap: 1,
              pl: 2,
              pr: 1,
              py: 1,
              borderRadius: 4,
              border: 1,
              borderColor: 'divider',
              boxShadow: '0 8px 24px rgba(15, 40, 50, 0.08)',
              transition: 'border-color 120ms',
              '&:focus-within': { borderColor: 'primary.main' },
            }}
          >
            <TextField
              placeholder="Plan a trip, compare flights, check the weather…"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              disabled={streaming}
              multiline
              maxRows={6}
              fullWidth
              variant="standard"
              slotProps={{
                input: { disableUnderline: true, sx: { py: 0.75 } },
                htmlInput: { 'aria-label': 'Message' },
              }}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault();
                  event.currentTarget.closest('form')?.requestSubmit();
                }
              }}
            />
            <IconButton
              type="submit"
              aria-label="Send"
              disabled={streaming || draft.trim() === ''}
              sx={{
                bgcolor: 'primary.main',
                color: 'primary.contrastText',
                '&:hover': { bgcolor: 'primary.dark' },
                '&.Mui-disabled': { bgcolor: 'action.disabledBackground' },
              }}
            >
              <ArrowUpwardRounded />
            </IconButton>
          </Paper>
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
