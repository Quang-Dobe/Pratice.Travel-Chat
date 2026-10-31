import '@testing-library/jest-dom/vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { NO_FORECAST, type Picked, type PlanCard as PlanCardData } from '../api/api';
import { PlanCard } from './PlanCard';

afterEach(cleanup);

const baseCard: PlanCardData = {
  id: 'card1',
  status: 'open',
  flights: [
    { id: 'f1', summary: 'Flight one', priceUsd: 500, deepLink: 'https://example.com/f1' },
    { id: 'f2', summary: 'Flight two', priceUsd: 900, deepLink: 'https://example.com/f2' },
  ],
  hotels: [
    { id: 'h1', summary: 'Hotel one', priceUsd: 300, deepLink: 'https://example.com/h1' },
    { id: 'h2', summary: 'Hotel two', priceUsd: 700, deepLink: 'https://example.com/h2' },
  ],
  picked: { flightId: 'f1', hotelId: 'h1' },
  days: [
    { date: '2026-11-01', activities: ['Temple'], weather: '18C, 10% rain', costUsd: 90 },
    { date: '2026-11-02', activities: ['Market'], weather: NO_FORECAST, costUsd: 90 },
  ],
  budget: {
    flightUsd: 500,
    hotelUsd: 300,
    dailyUsd: 180,
    totalUsd: 980,
    budgetUsd: 2000,
    overBudget: false,
  },
};

function Harness({
  card = baseCard,
  interactive = true,
  onChange,
}: {
  card?: PlanCardData;
  interactive?: boolean;
  onChange?: (picked: Picked) => void;
}) {
  const [picked, setPicked] = useState<Picked>(card.picked);
  return (
    <PlanCard
      card={card}
      picked={picked}
      interactive={interactive}
      onPick={(next) => {
        setPicked(next);
        onChange?.(next);
      }}
    />
  );
}

describe('PlanCard', () => {
  it('lists flights and hotels as radios named by their summary', () => {
    render(<Harness />);

    const card = screen.getByRole('group', { name: 'Plan card' });
    const flights = within(card).getByRole('radiogroup', { name: 'Flight options' });
    const hotels = within(card).getByRole('radiogroup', { name: 'Hotel options' });
    expect(within(flights).getByRole('radio', { name: 'Flight one' })).toBeChecked();
    expect(within(hotels).getByRole('radio', { name: 'Hotel one' })).toBeChecked();
    expect(within(hotels).getByRole('radio', { name: 'Hotel two' })).not.toBeChecked();
  });

  it('recomputes the total from the picked flight and hotel', async () => {
    const seen: Picked[] = [];
    render(<Harness onChange={(picked) => seen.push(picked)} />);
    const budget = screen.getByRole('region', { name: 'Budget' });
    expect(within(budget).getByText('Total $980.00 of $2,000.00 budget')).toBeInTheDocument();
    expect(within(budget).getByText('Flight $500.00')).toBeInTheDocument();
    expect(within(budget).getByText('Hotel $300.00')).toBeInTheDocument();
    expect(within(budget).getByText('Daily $180.00')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('radio', { name: 'Hotel two' }));

    expect(within(budget).getByText('Total $1,380.00 of $2,000.00 budget')).toBeInTheDocument();
    expect(within(budget).getByText('Hotel $700.00')).toBeInTheDocument();
    expect(seen.at(-1)).toEqual({ flightId: 'f1', hotelId: 'h2' });

    await userEvent.click(screen.getByRole('radio', { name: 'Flight two' }));

    expect(within(budget).getByText('Total $1,780.00 of $2,000.00 budget')).toBeInTheDocument();
    expect(within(budget).getByText('Flight $900.00')).toBeInTheDocument();
    expect(within(budget).queryByText(/not counted/)).not.toBeInTheDocument();
    expect(within(budget).queryByRole('alert')).not.toBeInTheDocument();
    expect(seen.at(-1)).toEqual({ flightId: 'f2', hotelId: 'h2' });
  });

  it('warns when the live total goes over the budget', async () => {
    const card = { ...baseCard, budget: { ...baseCard.budget, budgetUsd: 1200 } };
    render(<Harness card={card} />);
    const budget = screen.getByRole('region', { name: 'Budget' });
    expect(within(budget).queryByRole('alert')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('radio', { name: 'Hotel two' }));

    expect(within(budget).getByRole('alert')).toHaveTextContent(/over budget/i);
  });

  it('shows an estimated total and never warns when the user set no budget', async () => {
    const card = { ...baseCard, budget: { ...baseCard.budget, budgetUsd: null } };
    render(<Harness card={card} />);
    const budget = screen.getByRole('region', { name: 'Budget' });
    expect(within(budget).getByText('Estimated total $980.00')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('radio', { name: 'Flight two' }));
    await userEvent.click(screen.getByRole('radio', { name: 'Hotel two' }));

    expect(within(budget).getByText('Estimated total $1,780.00')).toBeInTheDocument();
    expect(within(budget).queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows each day with activities, weather and cost', () => {
    render(<Harness />);

    const first = screen.getByRole('region', { name: 'Day 2026-11-01' });
    expect(within(first).getByText('Temple')).toBeInTheDocument();
    expect(within(first).getByText('18C, 10% rain')).toBeInTheDocument();
    expect(within(first).getByText('$90.00')).toBeInTheDocument();
    const second = screen.getByRole('region', { name: 'Day 2026-11-02' });
    expect(within(second).getByText(NO_FORECAST)).toBeInTheDocument();
  });

  it('opens booking links in a new tab with https hrefs', () => {
    render(<Harness />);

    const link = screen.getByRole('link', { name: 'Book Flight two' });
    expect(link).toHaveAttribute('href', 'https://example.com/f2');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(screen.getAllByRole('link')).toHaveLength(4);
  });

  it('shows a replaced card read-only and dimmed', async () => {
    const onChange: Picked[] = [];
    render(
      <Harness
        card={{ ...baseCard, status: 'replaced' }}
        interactive={false}
        onChange={(picked) => onChange.push(picked)}
      />,
    );

    expect(screen.getByText('Replaced')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Hotel two' })).toBeDisabled();
    await userEvent
      .setup({ pointerEventsCheck: 0 })
      .click(screen.getByRole('radio', { name: 'Hotel two' }));
    expect(onChange).toEqual([]);
  });

  it('shows an approved card with the server total and the approved picks', () => {
    const card: PlanCardData = {
      ...baseCard,
      status: 'approved',
      picked: { flightId: 'f2', hotelId: 'h2' },
      budget: {
        flightUsd: 900,
        hotelUsd: 700,
        dailyUsd: 180,
        totalUsd: 1780,
        budgetUsd: 600,
        overBudget: true,
      },
    };
    render(
      <PlanCard card={card} picked={card.picked} interactive={false} onPick={() => undefined} />,
    );

    expect(screen.getByText('Approved')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Hotel two' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Hotel two' })).toBeDisabled();
    expect(screen.getByText('Total $1,780.00 of $600.00 budget')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(/over budget/i);
  });
});
