import ArrowUpwardRounded from '@mui/icons-material/ArrowUpwardRounded';
import { IconButton, Paper, TextField } from '@mui/material';
import type { FormEvent } from 'react';

type ComposerProps = {
  value: string;
  disabled: boolean;
  onChange: (text: string) => void;
  onSend: () => void;
};

export function Composer({ value, disabled, onChange, onSend }: ComposerProps) {
  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    onSend();
  }

  return (
    <Paper
      component="form"
      onSubmit={handleSubmit}
      elevation={0}
      sx={{
        display: 'flex',
        alignItems: 'flex-end',
        gap: 1,
        pl: 2,
        pr: 1,
        py: 1,
        borderRadius: 4,
        border: 1,
        borderColor: 'divider',
        boxShadow: '0 8px 24px rgba(15, 40, 50, 0.08)',
        transition: 'border-color 120ms',
        '&:focus-within': { borderColor: 'primary.main' },
      }}
    >
      <TextField
        placeholder="Plan a trip, compare flights, check the weather…"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
        multiline
        maxRows={6}
        fullWidth
        variant="standard"
        slotProps={{
          input: { disableUnderline: true, sx: { py: 0.75 } },
          htmlInput: { 'aria-label': 'Message' },
        }}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            event.currentTarget.closest('form')?.requestSubmit();
          }
        }}
      />
      <IconButton
        type="submit"
        aria-label="Send"
        disabled={disabled || value.trim() === ''}
        sx={{
          bgcolor: 'primary.main',
          color: 'primary.contrastText',
          '&:hover': { bgcolor: 'primary.dark' },
          '&.Mui-disabled': { bgcolor: 'action.disabledBackground' },
        }}
      >
        <ArrowUpwardRounded />
      </IconButton>
    </Paper>
  );
}
