import {
  Alert,
  Avatar,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  Stack,
  Switch,
  Typography,
} from '@mui/material';
import { useEffect, useState } from 'react';
import { useRevalidator } from 'react-router';
import { clearStarters, getStarters, setStarters } from '../api/api';
import type { Me } from '../api/schemas';
import { initials } from './initials';

type ProfileDialogProps = {
  me: Me;
  open: boolean;
  onClose: () => void;
};

type Feedback = { severity: 'error' | 'success'; text: string };

const CLEARED_NOTE = 'Suggestion history cleared.';

function messageOf(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

export function ProfileDialog({ me, open, onClose }: ProfileDialogProps) {
  const revalidator = useRevalidator();
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<Feedback | null>(null);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setEnabled(null);
    setFeedback(null);
    getStarters(controller.signal)
      .then((starters) => setEnabled(starters.enabled))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setFeedback({
          severity: 'error',
          text: messageOf(error, 'Could not load suggestion settings.'),
        });
      });
    return () => controller.abort();
  }, [open]);

  async function toggle(next: boolean) {
    setBusy(true);
    setFeedback(null);
    try {
      setEnabled(await setStarters(next));
      revalidator.revalidate();
    } catch (error) {
      setFeedback({
        severity: 'error',
        text: messageOf(error, 'Could not update suggestion settings.'),
      });
    } finally {
      setBusy(false);
    }
  }

  async function clear() {
    setBusy(true);
    setFeedback(null);
    try {
      await clearStarters();
      setFeedback({ severity: 'success', text: CLEARED_NOTE });
      revalidator.revalidate();
    } catch (error) {
      setFeedback({
        severity: 'error',
        text: messageOf(error, 'Could not clear suggestion history.'),
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="xs"
      slotProps={{ paper: { sx: { borderRadius: 4 } } }}
    >
      <DialogTitle>Profile</DialogTitle>
      <DialogContent>
        <Stack direction="row" spacing={2} sx={{ alignItems: 'center' }}>
          <Avatar sx={{ width: 56, height: 56, bgcolor: 'primary.main', fontSize: 22 }}>
            {initials(me.name)}
          </Avatar>
          <Stack sx={{ minWidth: 0 }}>
            <Typography variant="subtitle1" sx={{ fontWeight: 600 }} noWrap>
              {me.name}
            </Typography>
            <Typography color="text.secondary" noWrap>
              {me.email}
            </Typography>
          </Stack>
        </Stack>
        <Stack spacing={1.5} sx={{ mt: 3, alignItems: 'flex-start' }}>
          <FormControlLabel
            control={
              <Switch
                checked={enabled ?? false}
                disabled={enabled === null || busy}
                onChange={(event) => void toggle(event.target.checked)}
              />
            }
            label="Suggestions from my past trips"
          />
          <Button variant="outlined" size="small" disabled={busy} onClick={() => void clear()}>
            Clear suggestion history
          </Button>
          {feedback && (
            <Alert severity={feedback.severity} sx={{ alignSelf: 'stretch' }}>
              {feedback.text}
            </Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
}
