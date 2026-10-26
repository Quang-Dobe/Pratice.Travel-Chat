import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Typography,
} from '@mui/material';
import type { Me } from '../api/api';

type ProfileDialogProps = {
  me: Me;
  open: boolean;
  onClose: () => void;
};

export function ProfileDialog({ me, open, onClose }: ProfileDialogProps) {
  return (
    <Dialog open={open} onClose={onClose}>
      <DialogTitle>Profile</DialogTitle>
      <DialogContent>
        <Typography>{me.name}</Typography>
        <Typography color="text.secondary">{me.email}</Typography>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
}
