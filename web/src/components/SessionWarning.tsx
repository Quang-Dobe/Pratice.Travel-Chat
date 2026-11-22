import { Alert, Button } from '@mui/material';

const SECONDS_PER_MINUTE = 60;

type SessionWarningProps = {
  secondsLeft: number;
  busy: boolean;
  onStay: () => void;
};

function timeLeftText(secondsLeft: number): string {
  if (secondsLeft < SECONDS_PER_MINUTE) {
    return 'in less than a minute';
  }
  const minutes = Math.ceil(secondsLeft / SECONDS_PER_MINUTE);
  return `in ${minutes} ${minutes === 1 ? 'minute' : 'minutes'}`;
}

export function SessionWarning({ secondsLeft, busy, onStay }: SessionWarningProps) {
  return (
    <Alert
      severity="warning"
      sx={{ mb: 1.5 }}
      action={
        <Button color="inherit" size="small" disabled={busy} onClick={onStay}>
          Stay signed in
        </Button>
      }
    >
      {`Your session ends ${timeLeftText(secondsLeft)}`}
    </Alert>
  );
}
