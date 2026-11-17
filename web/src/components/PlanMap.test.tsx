import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Leg, PlanCard } from '../api/api';
import { makeCard as makeBaseCard } from '../test/planCard';
import { PlanMap } from './PlanMap';

const { fitBounds, fakeMap, layerBehavior } = vi.hoisted(() => {
  const fit = vi.fn();
  return {
    fitBounds: fit,
    fakeMap: { fitBounds: fit },
    layerBehavior: { throws: false },
  };
});

vi.mock('react-map-gl/maplibre', () => ({
  Map: ({
    mapStyle,
    onMove,
    onError,
    children,
  }: {
    mapStyle: string;
    onMove: (event: { viewState: { zoom: number } }) => void;
    onError: () => void;
    children: ReactNode;
  }) => (
    <div data-testid="map" data-style={mapStyle}>
      <button type="button" onClick={() => onMove({ viewState: { zoom: 7 } })}>
        zoom
      </button>
      <button type="button" onClick={onError}>
        fail load
      </button>
      {children}
    </div>
  ),
  Marker: ({ children }: { children: ReactNode }) => <div data-testid="marker">{children}</div>,
  Source: ({ data, children }: { data: { features: unknown[] }; children: ReactNode }) => (
    <div data-testid="legs-source" data-features={data.features.length}>
      {children}
    </div>
  ),
  Layer: () => {
    if (layerBehavior.throws) {
      throw new Error('map is dead');
    }
    return <div data-testid="layer" />;
  },
  NavigationControl: () => <div data-testid="nav" />,
  useMap: () => ({ current: fakeMap }),
}));

beforeEach(() => {
  layerBehavior.throws = false;
  fitBounds.mockReset();
});

const placedLeg: Leg = {
  from: { code: 'HND', name: 'Haneda', lat: 35.55, lon: 139.78 },
  to: { code: 'SIN', name: 'Changi', lat: 1.36, lon: 103.99 },
  departs: '2026-11-01T08:10:00',
  arrives: '2026-11-01T14:25:00',
};

const unplacedLeg: Leg = {
  from: { code: 'SIN', name: 'Changi', lat: null, lon: null },
  to: { code: 'BKK', name: 'Suvarnabhumi', lat: null, lon: null },
  departs: '2026-11-01T16:00:00',
  arrives: '2026-11-01T17:30:00',
};

function makeCard(overrides: Partial<PlanCard> = {}): PlanCard {
  return makeBaseCard({
    flights: [
      {
        id: 'f1',
        summary: 'Flight one',
        priceUsd: 500,
        deepLink: 'https://example.com/f1',
        legs: [placedLeg, unplacedLeg],
        location: null,
      },
      {
        id: 'f2',
        summary: 'Flight two',
        priceUsd: 650,
        deepLink: 'https://example.com/f2',
        legs: [
          {
            ...placedLeg,
            departs: '2026-11-01T21:00:00',
            arrives: '2026-11-02T03:15:00',
          },
        ],
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
        location: { lat: 1.29, lon: 103.85 },
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
    days: [
      {
        date: '2026-11-01',
        activities: ['Temple'],
        weather: '18C',
        costUsd: 90,
        stops: [{ name: 'Gardens', lat: 1.28, lon: 103.86 }],
      },
    ],
    reviewPins: [{ name: 'Ramen Bar', kind: 'restaurant', lat: 1.3, lon: 103.8 }],
    ...overrides,
  });
}

const noPoints = makeCard({
  flights: [
    {
      id: 'f1',
      summary: 'Flight one',
      priceUsd: 500,
      deepLink: 'https://example.com/f1',
      legs: [unplacedLeg],
      location: null,
    },
  ],
  hotels: [],
  days: [{ date: '2026-11-01', activities: [], weather: '', costUsd: 0, stops: [] }],
  reviewPins: [],
});

describe('PlanMap', () => {
  it('draws the map with markers and lines from the gateway style url', async () => {
    render(<PlanMap card={makeCard()} pickedFlightId="f1" />);

    const region = await screen.findByRole('region', { name: 'Trip map' });
    const style = within(region).getByTestId('map').getAttribute('data-style') ?? '';
    expect(style.endsWith('/api/maps/v1/styles/osm-bright/style.json')).toBe(true);
    expect(style).not.toContain('apiKey');
    expect(within(region).getByTestId('legs-source')).toHaveAttribute('data-features', '1');
    expect(
      within(region)
        .getAllByTitle(/.+/)
        .map((marker) => marker.getAttribute('title')),
    ).toEqual(
      expect.arrayContaining([
        'Hotel: Hotel one',
        'Day 1 stop 1: Gardens',
        'Restaurant: Ramen Bar',
      ]),
    );
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows a notice and keeps the lists when the map reports an error', async () => {
    render(<PlanMap card={makeCard()} pickedFlightId="f1" />);

    await userEvent.click(await screen.findByRole('button', { name: 'fail load' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('The map is unavailable right now.');
    expect(screen.queryByTestId('map')).not.toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'Map points' })).toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'Flight legs' })).toBeInTheDocument();
  });

  it('shows the notice and keeps the lists when the map throws while rendering', async () => {
    layerBehavior.throws = true;
    const logged = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    try {
      render(<PlanMap card={makeCard()} pickedFlightId="f1" />);

      expect(await screen.findByRole('alert')).toHaveTextContent(
        'The map is unavailable right now.',
      );
      expect(screen.queryByRole('region', { name: 'Trip map' })).not.toBeInTheDocument();
      expect(screen.getByRole('list', { name: 'Map points' })).toBeInTheDocument();
      expect(screen.getByRole('list', { name: 'Flight legs' })).toBeInTheDocument();
    } finally {
      logged.mockRestore();
    }
  });

  it('shows a notice and no map when no point has coordinates', async () => {
    render(<PlanMap card={noPoints} pickedFlightId="f1" />);

    expect(await screen.findByRole('alert')).toHaveTextContent('The map is unavailable right now.');
    expect(screen.queryByTestId('map')).not.toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Map points' })).not.toBeInTheDocument();
  });

  it('lists each leg of the picked flight with its times', async () => {
    const { rerender } = render(<PlanMap card={makeCard()} pickedFlightId="f1" />);

    const legs = screen.getByRole('list', { name: 'Flight legs' });
    expect(
      within(legs)
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual(['HND → SIN · 08:10 – 14:25', 'SIN → BKK · 16:00 – 17:30']);

    rerender(<PlanMap card={makeCard()} pickedFlightId="f2" />);

    expect(
      within(screen.getByRole('list', { name: 'Flight legs' }))
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual(['HND → SIN · 21:00 – 03:15']);
    expect(await screen.findByRole('region', { name: 'Trip map' })).toBeInTheDocument();
  });

  it('shows only the clock for space-separated leg times', async () => {
    const spaced: Leg = {
      ...placedLeg,
      departs: '2026-12-01 08:10',
      arrives: '2026-12-01 14:25',
    };
    const base = makeCard();
    const card = makeCard({
      flights: [{ ...(base.flights[0] as (typeof base.flights)[number]), legs: [spaced] }],
    });
    render(<PlanMap card={card} pickedFlightId="f1" />);

    expect(
      within(screen.getByRole('list', { name: 'Flight legs' }))
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual(['HND → SIN · 08:10 – 14:25']);
    await screen.findByRole('region', { name: 'Trip map' });
  });

  it('names every pin by type in the hidden Map points list', async () => {
    render(<PlanMap card={makeCard()} pickedFlightId="f1" />);

    const points = screen.getByRole('list', { name: 'Map points' });
    expect(
      within(points)
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual(['Hotel: Hotel one', 'Day 1 stop 1: Gardens', 'Restaurant: Ramen Bar']);
    await screen.findByRole('region', { name: 'Trip map' });
  });

  it('keeps the same map mounted and refits the bounds when pins arrive', async () => {
    const { rerender } = render(<PlanMap card={makeCard()} pickedFlightId="f1" />);
    const map = await screen.findByTestId('map');
    await waitFor(() => expect(fitBounds).toHaveBeenCalledTimes(1));
    const before = fitBounds.mock.calls[0]?.[0] as number[][];

    rerender(
      <PlanMap
        card={makeCard({
          reviewPins: [
            { name: 'Ramen Bar', kind: 'restaurant', lat: 1.3, lon: 103.8 },
            { name: 'Far Cafe', kind: 'place', lat: 40, lon: 103.8 },
          ],
        })}
        pickedFlightId="f1"
      />,
    );

    expect(screen.getByTestId('map')).toBe(map);
    await waitFor(() => expect(fitBounds).toHaveBeenCalledTimes(2));
    const after = fitBounds.mock.calls[1]?.[0] as number[][];
    expect(after[1]?.[1]).toBeGreaterThan(before[1]?.[1] ?? Number.POSITIVE_INFINITY);
  });

  it('keeps data-zoom current from map moves', async () => {
    render(<PlanMap card={makeCard()} pickedFlightId="f1" />);
    const region = await screen.findByRole('region', { name: 'Trip map' });

    await userEvent.click(screen.getByRole('button', { name: 'zoom' }));

    expect(region).toHaveAttribute('data-zoom', '7');
  });
});
