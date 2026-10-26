import { Box, Button, Typography } from '@mui/material';

export function LoginPage() {
  return (
    <Box
      sx={{
        minHeight: '100vh',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 3,
      }}
    >
      <Typography variant="h4" component="h1">
        Travel Chat
      </Typography>
      <Button variant="contained" onClick={() => window.location.assign('/auth/login')}>
        Sign in
      </Button>
    </Box>
  );
}
