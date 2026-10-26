import { Alert, Box, Button, Paper, Stack, TextField, Typography } from '@mui/material';
import { type FormEvent, useEffect, useRef, useState } from 'react';
import { useLoaderData, useNavigate, useRevalidator } from 'react-router';
import {
  type Conversation,
  createConversation,
  type Message,
  sendMessage,
  UnauthorizedError,
} from '../api/api';

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}

function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === 'user';
  return (
    <Paper
      variant="outlined"
      sx={{ p: 1.5, maxWidth: '75%', alignSelf: isUser ? 'flex-end' : 'flex-start' }}
    >
      <Typography variant="caption" color="text.secondary" component="p">
        {isUser ? 'You' : 'Assistant'}
      </Typography>
      <Typography sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
        {message.text}
      </Typography>
    </Paper>
  );
}

function ChatView({ conversation }: { conversation: Conversation | undefined }) {
  const navigate = useNavigate();
  const revalidator = useRevalidator();
  const [conversationId, setConversationId] = useState(conversation?.id);
  const [messages, setMessages] = useState<Message[]>(conversation?.messages ?? []);
  const [draft, setDraft] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

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

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || streaming) {
      return;
    }
    setDraft('');
    void send(text);
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      <Stack
        role="log"
        aria-label="Conversation"
        spacing={1.5}
        sx={{ flexGrow: 1, overflowY: 'auto', p: 2 }}
      >
        {messages.map((message, index) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: messages are append-only with no stable id
          <MessageBubble key={index} message={message} />
        ))}
      </Stack>
      {error && (
        <Alert severity="error" sx={{ mx: 2 }}>
          {error}
        </Alert>
      )}
      <Box component="form" onSubmit={handleSubmit} sx={{ display: 'flex', gap: 1, p: 2 }}>
        <TextField
          label="Message"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          disabled={streaming}
          multiline
          maxRows={6}
          fullWidth
          size="small"
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault();
              event.currentTarget.closest('form')?.requestSubmit();
            }
          }}
        />
        <Button type="submit" variant="contained" disabled={streaming || draft.trim() === ''}>
          Send
        </Button>
      </Box>
    </Box>
  );
}

export function ChatPage() {
  const conversation = useLoaderData<Conversation | null | undefined>();

  if (conversation === null) {
    return (
      <Box sx={{ p: 3 }}>
        <Typography variant="h6" component="h1">
          Conversation not found
        </Typography>
      </Box>
    );
  }

  return <ChatView key={conversation?.id ?? 'new'} conversation={conversation} />;
}
