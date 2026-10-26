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
  type AppData,
  type Conversation,
  createConversation,
  type Message,
  sendMessage,
  UnauthorizedError,
} from '../api/api';
import { initials } from '../components/initials';
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

function MessageBubble({ message, userInitials }: { message: Message; userInitials: string }) {
  const isUser = message.role === 'user';
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
          borderRadius: 3,
          ...(isUser
            ? { bgcolor: 'primary.main', color: 'primary.contrastText', borderTopRightRadius: 4 }
            : { border: 1, borderColor: 'divider', borderTopLeftRadius: 4 }),
        }}
      >
        <Typography variant="caption" component="p" sx={{ fontWeight: 600, opacity: 0.75 }}>
          {isUser ? 'You' : 'Assistant'}
        </Typography>
        <Typography component="p" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
          {message.text === '' && !isUser ? <TypingDots /> : message.text}
        </Typography>
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
  const [messages, setMessages] = useState<Message[]>(conversation?.messages ?? []);
  const [draft, setDraft] = useState('');
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

  function appendToken(text: string) {
    setMessages((previous) =>
      previous.map((message, index) =>
        index === previous.length - 1 ? { ...message, text: message.text + text } : message,
      ),
    );
  }

  async function send(text: string) {
    const controller = new AbortController();
    abortRef.current = controller;
    setError(null);
    setStreaming(true);
    const isNew = conversationId === undefined;
    let id = conversationId;
    let appended = false;
    try {
      if (id === undefined) {
        id = (await createConversation(text)).id;
        setConversationId(id);
      }
      setMessages((previous) => [
        ...previous,
        { role: 'user', text },
        { role: 'assistant', text: '' },
      ]);
      appended = true;
      for await (const event of sendMessage(id, text, controller.signal)) {
        if (event.type === 'token') {
          appendToken(event.text);
        }
      }
    } catch (caught) {
      if (appended) {
        setMessages((previous) => previous.slice(0, -2));
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

  function submit(text: string) {
    if (!text || streaming) {
      return;
    }
    setDraft('');
    void send(text);
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    submit(draft.trim());
  }

  const isEmpty = messages.length === 0;
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
            // biome-ignore lint/suspicious/noArrayIndexKey: messages are append-only with no stable id
            <MessageBubble key={index} message={message} userInitials={userInitials} />
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
