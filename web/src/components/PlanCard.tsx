import {
  Alert,
  Box,
  Chip,
  Divider,
  FormControlLabel,
  Link,
  Paper,
  Radio,
  RadioGroup,
  Stack,
  Typography,
} from '@mui/material';
import { NO_FORECAST, type Option, type Picked, type PlanCard as PlanCardData } from '../api/api';

const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });

function priceOf(options: Option[], id: string): number {
  return options.find((option) => option.id === id)?.priceUsd ?? 0;
}

type OptionListProps = {
  label: string;
  heading: string;
  options: Option[];
  value: string;
  disabled: boolean;
  onChange: (id: string) => void;
};

function OptionList({ label, heading, options, value, disabled, onChange }: OptionListProps) {
  if (options.length === 0) {
    return null;
  }
  return (
    <Box>
      <Typography variant="subtitle2" component="h4">
        {heading}
      </Typography>
      <RadioGroup
        aria-label={label}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map((option) => (
          <Stack
            key={option.id}
            direction="row"
            sx={{ alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}
          >
            <FormControlLabel
              value={option.id}
              control={<Radio />}
              label={option.summary}
              disabled={disabled}
              sx={{ flexGrow: 1, mr: 0 }}
            />
            <Typography variant="body2" component="span" sx={{ fontWeight: 600 }}>
              {usd.format(option.priceUsd)}
            </Typography>
            <Link
              href={option.deepLink}
              target="_blank"
              rel="noopener noreferrer"
              underline="always"
              aria-label={`Book ${option.summary}`}
            >
              Book
            </Link>
          </Stack>
        ))}
      </RadioGroup>
    </Box>
  );
}

type PlanCardProps = {
  card: PlanCardData;
  picked: Picked;
  interactive: boolean;
  onPick: (picked: Picked) => void;
};

export function PlanCard({ card, picked, interactive, onPick }: PlanCardProps) {
  const { status, budget } = card;
  const open = status === 'open';
  const hotelUsd = priceOf(card.hotels, picked.hotelId);
  const totalUsd = open ? hotelUsd + budget.dailyUsd : budget.totalUsd;
  const overBudget = open ? totalUsd > budget.budgetUsd : budget.overBudget;

  return (
    <Paper
      variant="outlined"
      role="group"
      aria-label="Plan card"
      sx={{ mt: 1.5, p: 1.5, borderRadius: 2, opacity: status === 'replaced' ? 0.6 : 1 }}
    >
      <Stack direction="row" sx={{ alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
        <Typography variant="subtitle2" component="h3">
          Trip plan
        </Typography>
        {status === 'approved' && <Chip size="small" color="success" label="Approved" />}
        {status === 'replaced' && <Chip size="small" label="Replaced" />}
      </Stack>

      <Stack spacing={1.5}>
        <OptionList
          label="Flight options"
          heading="Flights"
          options={card.flights}
          value={picked.flightId}
          disabled={!interactive}
          onChange={(flightId) => onPick({ ...picked, flightId })}
        />
        <OptionList
          label="Hotel options"
          heading="Hotels"
          options={card.hotels}
          value={picked.hotelId}
          disabled={!interactive}
          onChange={(hotelId) => onPick({ ...picked, hotelId })}
        />

        {card.days.map((day) => (
          <Box key={day.date} component="section" aria-label={`Day ${day.date}`}>
            <Stack direction="row" sx={{ justifyContent: 'space-between', gap: 2 }}>
              <Typography variant="subtitle2" component="h4">
                {day.date}
              </Typography>
              <Typography variant="body2" component="span">
                {usd.format(day.costUsd)}
              </Typography>
            </Stack>
            <Box component="ul" sx={{ my: 0.5, pl: 2.5 }}>
              {day.activities.map((activity, index) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: activities can repeat and have no id
                <li key={index}>
                  <Typography variant="body2" component="span">
                    {activity}
                  </Typography>
                </li>
              ))}
            </Box>
            <Typography variant="caption" component="span" color="text.secondary">
              {day.weather || NO_FORECAST}
            </Typography>
          </Box>
        ))}

        <Divider />
        <Box component="section" aria-label="Budget">
          <Typography variant="subtitle2" component="h4">
            {`Total ${usd.format(totalUsd)} of ${usd.format(budget.budgetUsd)} budget`}
          </Typography>
          <Stack direction="row" sx={{ gap: 2, flexWrap: 'wrap', mt: 0.5 }}>
            <Typography variant="body2" component="span">
              {`Hotel ${usd.format(hotelUsd)}`}
            </Typography>
            <Typography variant="body2" component="span">
              {`Daily ${usd.format(budget.dailyUsd)}`}
            </Typography>
          </Stack>
          <Typography variant="caption" component="span" color="text.secondary">
            Flights are listed for booking and are not counted in the total.
          </Typography>
          {overBudget && (
            <Alert severity="warning" sx={{ mt: 1 }}>
              This plan is over budget.
            </Alert>
          )}
        </Box>
      </Stack>
    </Paper>
  );
}
