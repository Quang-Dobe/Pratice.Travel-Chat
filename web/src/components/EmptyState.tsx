import AutoAwesomeRounded from '@mui/icons-material/AutoAwesomeRounded';
import BeachAccessRounded from '@mui/icons-material/BeachAccessRounded';
import LandscapeRounded from '@mui/icons-material/LandscapeRounded';
import RamenDiningRounded from '@mui/icons-material/RamenDiningRounded';
import SavingsRounded from '@mui/icons-material/SavingsRounded';
import { Box, Card, CardActionArea, Typography } from '@mui/material';
import type { ReactNode } from 'react';
import { BrandMark } from './BrandMark';

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

export function EmptyState({ name, disabled, onPick }: EmptyStateProps) {
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
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 2 }}>
          {starters.map((starter) => (
            <Card
              key={starter.title}
              variant="outlined"
              sx={{ borderRadius: 4, textAlign: 'left' }}
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
