import TravelExploreRounded from '@mui/icons-material/TravelExploreRounded';
import { Alert, Avatar, Box, Paper, Typography } from '@mui/material';
import { keyframes } from '@mui/material/styles';
import type { Picked, Pill } from '../api/schemas';
import type { ChatMessage } from '../pages/chatMessages';
import { ExportLink } from './ExportLink';
import { PlanCard } from './PlanCard';
import { ReviewSources } from './ReviewSources';
import { SuggestionPills } from './SuggestionPills';
import { ToolProgress } from './ToolProgress';

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

type MessageBubbleProps = {
  message: ChatMessage;
  userInitials: string;
  isLast: boolean;
  streaming: boolean;
  cardInteractive: boolean;
  showMap: boolean;
  picked: Picked | null;
  onPickPill: (pill: Pill) => void;
  onPickOptions: (cardId: string, picked: Picked) => void;
};

export function MessageBubble({
  message,
  userInitials,
  isLast,
  streaming,
  cardInteractive,
  showMap,
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
          px: 2.5,
          py: 1.75,
          maxWidth: '75%',
          ...(planCard ? { width: '75%' } : {}),
          borderRadius: 4,
          ...(isUser
            ? { bgcolor: 'primary.main', color: 'primary.contrastText', borderTopRightRadius: 6 }
            : { border: 1, borderColor: 'divider', borderTopLeftRadius: 6 }),
        }}
      >
        <Typography variant="caption" component="p" sx={{ fontWeight: 600, opacity: 0.75 }}>
          {isUser ? 'You' : 'Assistant'}
        </Typography>
        {!isUser && <ToolProgress toolCalls={message.toolCalls} />}
        <Typography component="p" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
          {showDots ? <TypingDots /> : message.text}
        </Typography>
        {!isUser && <ReviewSources items={message.sources ?? []} />}
        {(message.artifacts ?? []).map((artifact) => (
          <ExportLink key={artifact.id} artifact={artifact} />
        ))}
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
            showMap={showMap}
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
