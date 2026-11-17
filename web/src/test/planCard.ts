import type { PlanCard } from '../api/api';

export function makeCard(overrides: Partial<PlanCard> = {}): PlanCard {
  return {
    id: 'card1',
    status: 'open',
    flights: [
      {
        id: 'f1',
        summary: 'Flight one',
        priceUsd: 500,
        deepLink: 'https://example.com/f1',
        legs: [],
        location: null,
      },
      {
        id: 'f2',
        summary: 'Flight two',
        priceUsd: 650,
        deepLink: 'https://example.com/f2',
        legs: [],
        location: null,
      },
    ],
    hotels: [
      {
        id: 'h1',
        summary: 'Hotel one',
        priceUsd: 300,
        deepLink: 'https://example.com/h1',
        legs: [],
        location: null,
      },
      {
        id: 'h2',
        summary: 'Hotel two',
        priceUsd: 700,
        deepLink: 'https://example.com/h2',
        legs: [],
        location: null,
      },
    ],
    picked: { flightId: 'f1', hotelId: 'h1' },
    days: [{ date: '2026-11-01', activities: ['Temple'], weather: '18C', costUsd: 90, stops: [] }],
    reviewPins: [],
    budget: {
      flightUsd: 500,
      hotelUsd: 300,
      dailyUsd: 90,
      totalUsd: 890,
      budgetUsd: 2000,
      overBudget: false,
    },
    ...overrides,
  };
}
