import AutoAwesomeRounded from '@mui/icons-material/AutoAwesomeRounded';
import BeachAccessRounded from '@mui/icons-material/BeachAccessRounded';
import HistoryRounded from '@mui/icons-material/HistoryRounded';
import LandscapeRounded from '@mui/icons-material/LandscapeRounded';
import RamenDiningRounded from '@mui/icons-material/RamenDiningRounded';
import SavingsRounded from '@mui/icons-material/SavingsRounded';
import { Box, Card, CardActionArea, Skeleton, Typography } from '@mui/material';
import type { ReactNode } from 'react';
import { BrandMark } from './BrandMark';

export const MAX_STARTERS = 4;

type Starter = { icon: ReactNode; title: string; prompt?: string };

const fixedStarters: Starter[] = [
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

const gridSx = {
  display: 'grid',
  gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' },
  gap: 2,
} as const;

type EmptyStateProps = {
  name: string | undefined;
  disabled: boolean;
  /** `null` while the past-trip starters are loading. */
  history: string[] | null;
  onPick: (prompt: string) => void;
};

function StarterCard({
  starter,
  disabled,
  onPick,
}: {
  starter: Starter;
  disabled: boolean;
  onPick: (prompt: string) => void;
}) {
  return (
    <Card variant="outlined" sx={{ borderRadius: 4, textAlign: 'left' }}>
      <CardActionArea
        disabled={disabled}
        onClick={() => onPick(starter.prompt ?? starter.title)}
        sx={{ p: 2, height: '100%', display: 'flex', alignItems: 'flex-start', gap: 1.5 }}
      >
        <Box sx={{ color: 'primary.main', display: 'flex', pt: 0.25 }}>{starter.icon}</Box>
        <Box sx={{ flexGrow: 1 }}>
          <Typography variant="subtitle2">{starter.title}</Typography>
          {starter.prompt && (
            <Typography variant="body2" color="text.secondary">
              {starter.prompt}
            </Typography>
          )}
        </Box>
      </CardActionArea>
    </Card>
  );
}

function StarterGrid({
  history,
  disabled,
  onPick,
}: Pick<EmptyStateProps, 'history' | 'disabled' | 'onPick'>) {
  if (history === null) {
    return (
      <Box sx={gridSx} role="status" aria-label="Loading suggestions">
        {fixedStarters.map((starter) => (
          <Skeleton key={starter.title} variant="rounded" height={88} sx={{ borderRadius: 4 }} />
        ))}
      </Box>
    );
  }
  if (history.length === 0) {
    return (
      <Box sx={gridSx}>
        {fixedStarters.map((starter) => (
          <StarterCard key={starter.title} starter={starter} disabled={disabled} onPick={onPick} />
        ))}
      </Box>
    );
  }
  return (
    <Box sx={{ textAlign: 'left' }}>
      <Typography variant="caption" color="text.secondary" component="p" sx={{ mb: 1 }}>
        From your past trips
      </Typography>
      <Box sx={gridSx} role="group" aria-label="Suggestions">
        {history.slice(0, MAX_STARTERS).map((text, index) => (
          <StarterCard
            key={`${index}-${text}`}
            starter={{ icon: <HistoryRounded />, title: text }}
            disabled={disabled}
            onPick={onPick}
          />
        ))}
      </Box>
    </Box>
  );
}

export function EmptyState({ name, disabled, history, onPick }: EmptyStateProps) {
  const firstName = name?.trim().split(/\s+/)[0];
  return (
    <Box
      sx={{ flexGrow: 1, display: 'grid', placeItems: 'center', px: 3, py: 4, overflowY: 'auto' }}
    >
      <Box sx={{ width: '100%', maxWidth: 720, textAlign: 'center' }}>
        <BrandMark size={56} radius={4} icon={<AutoAwesomeRounded />} sx={{ mx: 'auto', mb: 2 }} />
        <Typography variant="h4" component="h1">
          {firstName ? `Where to next, ${firstName}?` : 'Where to next?'}
        </Typography>
        <Typography color="text.secondary" sx={{ mt: 1.5, mb: 5 }}>
          Hi, I am your travel buddy. Tell me where, when and what you love, and we will plan a
          lovely trip together.
        </Typography>
        <StarterGrid history={history} disabled={disabled} onPick={onPick} />
      </Box>
    </Box>
  );
}
