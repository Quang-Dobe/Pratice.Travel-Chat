import { Box, Link as MuiLink, Typography } from '@mui/material';
import { Link } from 'react-router';

export function DeniedPage() {
  return (
    <Box
      sx={{
        minHeight: '100vh',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 2,
      }}
    >
      <Typography variant="h4" component="h1">
        Access is by invitation only
      </Typography>
      <Typography>Ask the owner of this app to invite your account, then try again.</Typography>
      <MuiLink component={Link} to="/login">
        Back to sign in
      </MuiLink>
    </Box>
  );
}
