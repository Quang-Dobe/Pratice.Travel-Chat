import { describe, expect, it } from 'vitest';
import type { Leg } from '../api/schemas';
import { makeCard } from '../test/planCard';
import { boundsOf, clock, collectPoints, collectRoutes } from './planMapData';

function leg(fromLat: number | null, toLat: number | null): Leg {
  return {
    from: { code: 'SGN', name: 'Saigon', lat: fromLat, lon: fromLat === null ? null : 106 },
    to: { code: 'HAN', name: 'Hanoi', lat: toLat, lon: toLat === null ? null : 105 },
    departs: '2026-11-01T08:30:00',
    arrives: '2026-11-01 10:45',
  };
}

describe('clock', () => {
  it('reads HH:MM after a T or a space', () => {
    expect(clock('2026-11-01T08:30:00')).toBe('08:30');
    expect(clock('2026-11-01 10:45')).toBe('10:45');
  });

  it('returns other strings unchanged', () => {
    expect(clock('soon')).toBe('soon');
  });
});

describe('boundsOf', () => {
  it('pads a single point by 0.02 each way', () => {
    const edges = boundsOf([{ lat: 10, lng: 20 }]);
    expect(edges.north).toBeCloseTo(10.02);
    expect(edges.south).toBeCloseTo(9.98);
    expect(edges.east).toBeCloseTo(20.02);
    expect(edges.west).toBeCloseTo(19.98);
  });

  it('spans several points without padding', () => {
    expect(
      boundsOf([
        { lat: 1, lng: 2 },
        { lat: 3, lng: 5 },
      ]),
    ).toEqual({ north: 3, south: 1, east: 5, west: 2 });
  });
});

describe('collectRoutes', () => {
  it('skips a leg with a null coordinate', () => {
    const routes = collectRoutes([leg(10, 20), leg(null, 20), leg(10, null)]);
    expect(routes).toHaveLength(1);
    expect(routes[0]?.key).toBe('leg-0');
  });
});

describe('collectPoints', () => {
  it('badges each kind and highlights only the picked hotel', () => {
    const card = makeCard({
      hotels: [
        {
          id: 'h1',
          summary: 'Hotel one',
          priceUsd: 300,
          deepLink: 'https://example.com/h1',
          legs: [],
          location: { lat: 1, lon: 2 },
        },
        {
          id: 'h2',
          summary: 'Hotel two',
          priceUsd: 700,
          deepLink: 'https://example.com/h2',
          legs: [],
          location: { lat: 3, lon: 4 },
        },
      ],
      days: [
        {
          date: '2026-11-01',
          activities: [],
          weather: '18C',
          costUsd: 0,
          stops: [{ name: 'Temple', lat: 5, lon: 6 }],
        },
      ],
      reviewPins: [
        { name: 'Pho', kind: 'restaurant', lat: 7, lon: 8 },
        { name: 'Lake', kind: 'place', lat: 9, lon: 10 },
      ],
    });
    const points = collectPoints(card, 'h2');
    expect(points.map((point) => point.badge)).toEqual(['H', 'H', '1.1', 'R', 'P']);
    expect(points.map((point) => point.highlighted)).toEqual([false, true, false, false, false]);
  });
});
