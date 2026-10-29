import CheckRounded from '@mui/icons-material/CheckRounded';
import { Chip, CircularProgress, Stack } from '@mui/material';
import type { ToolCall } from '../api/api';

export function ToolProgress({ toolCalls }: { toolCalls: ToolCall[] }) {
  if (toolCalls.length === 0) {
    return null;
  }
  return (
    <Stack
      direction="row"
      role="group"
      aria-label="Tool progress"
      sx={{ flexWrap: 'wrap', gap: 0.75, my: 0.75 }}
    >
      {toolCalls.map((call, index) => (
        <Chip
          // biome-ignore lint/suspicious/noArrayIndexKey: a tool can repeat within a turn and has no id
          key={index}
          size="small"
          variant="outlined"
          label={call.name}
          icon={
            call.status === 'started' ? (
              <CircularProgress size={12} aria-label="Running" />
            ) : (
              <CheckRounded aria-label="Done" />
            )
          }
        />
      ))}
    </Stack>
  );
}
