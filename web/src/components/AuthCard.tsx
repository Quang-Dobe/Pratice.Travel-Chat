import FlightTakeoffRounded from '@mui/icons-material/FlightTakeoffRounded';
import { Box, Paper } from '@mui/material';
import type { ReactNode } from 'react';
import { BrandMark } from './BrandMark';

export function AuthCard({ children }: { children: ReactNode }) {
  return (
    <Box
      sx={{
        minHeight: '100vh',
        display: 'grid',
        placeItems: 'center',
        p: 2,
        background:
          'radial-gradient(1200px 600px at 10% -10%, color-mix(in srgb, var(--mui-palette-primary-main) 22%, transparent), transparent), radial-gradient(900px 500px at 110% 110%, color-mix(in srgb, var(--mui-palette-secondary-main) 20%, transparent), transparent)',
      }}
    >
      <Paper
        elevation={0}
        sx={{
          width: '100%',
          maxWidth: 420,
          p: { xs: 4, sm: 5 },
          borderRadius: 5,
          border: 1,
          borderColor: 'divider',
          boxShadow: '0 24px 60px rgba(15, 40, 50, 0.12)',
          textAlign: 'center',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 2,
        }}
      >
        <BrandMark size={64} radius={4} icon={<FlightTakeoffRounded fontSize="large" />} />
        {children}
      </Paper>
    </Box>
  );
}
