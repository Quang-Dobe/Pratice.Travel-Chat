import { Button, Typography } from '@mui/material';
import { AuthCard } from '../components/AuthCard';

export function LoginPage() {
  return (
    <AuthCard>
      <Typography variant="h4" component="h1">
        Travel Chat
      </Typography>
      <Typography color="text.secondary">
        Plan trips by chatting: flights, hotels, weather and a day-by-day itinerary.
      </Typography>
      <Button
        variant="contained"
        size="large"
        fullWidth
        sx={{ mt: 1, py: 1.25 }}
        onClick={() => window.location.assign('/auth/login')}
      >
        Sign in
      </Button>
      <Typography variant="caption" color="text.secondary">
        Use your work account. Access is by invitation.
      </Typography>
    </AuthCard>
  );
}
