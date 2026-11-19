import { Box, type SxProps, type Theme } from '@mui/material';
import type { ReactNode } from 'react';
import { brandGradient } from '../theme';

type BrandMarkProps = {
  size: number;
  radius: number;
  icon: ReactNode;
  sx?: SxProps<Theme>;
};

export function BrandMark({ size, radius, icon, sx }: BrandMarkProps) {
  return (
    <Box
      sx={[
        {
          width: size,
          height: size,
          borderRadius: radius,
          display: 'grid',
          placeItems: 'center',
          color: '#fff',
          background: brandGradient,
        },
        ...(Array.isArray(sx) ? sx : [sx]),
      ]}
    >
      {icon}
    </Box>
  );
}
