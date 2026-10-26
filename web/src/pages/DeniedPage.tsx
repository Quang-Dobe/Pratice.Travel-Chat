import { Button, Typography } from '@mui/material';
import { Link } from 'react-router';
import { AuthCard } from '../components/AuthCard';

export function DeniedPage() {
  return (
    <AuthCard>
      <Typography variant="h5" component="h1">
        Access is by invitation only
      </Typography>
      <Typography color="text.secondary">
        Ask the owner of this app to invite your account, then try again.
      </Typography>
      <Button component={Link} to="/login" variant="outlined" fullWidth sx={{ mt: 1 }}>
        Back to sign in
      </Button>
    </AuthCard>
  );
}
