import { Chip, Stack } from '@mui/material';
import type { Pill } from '../api/schemas';

type SuggestionPillsProps = {
  items: Pill[];
  disabled: boolean;
  onPick: (pill: Pill) => void;
};

export function SuggestionPills({ items, disabled, onPick }: SuggestionPillsProps) {
  if (items.length === 0) {
    return null;
  }
  return (
    <Stack
      direction="row"
      role="group"
      aria-label="Suggestions"
      sx={{ flexWrap: 'wrap', gap: 0.75, mt: 1 }}
    >
      {items.map((pill, index) => (
        <Chip
          key={`${index}-${pill.action ?? 'plain'}-${pill.text}`}
          label={pill.text}
          color="primary"
          variant="outlined"
          clickable
          disabled={disabled}
          onClick={() => onPick(pill)}
        />
      ))}
    </Stack>
  );
}
