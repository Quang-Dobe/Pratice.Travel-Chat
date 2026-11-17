import type { Leg, PlanCard } from '../api/schemas';

export const HOTEL_COLOR = '#1565c0';
export const STOP_COLOR = '#2e7d32';
export const REVIEW_COLOR = '#ef6c00';
export const FLIGHT_COLOR = '#455a64';

export type PointKind = 'hotel' | 'stop' | 'restaurant' | 'place';

export type MapPoint = {
  key: string;
  kind: PointKind;
  description: string;
  badge: string;
  lat: number;
  lon: number;
  highlighted: boolean;
};

export type LatLng = { lat: number; lng: number };

export type Route = {
  key: string;
  from: LatLng;
  to: LatLng;
  middle: LatLng;
  times: string;
};

export function clock(value: string): string {
  return /[T ](\d{2}:\d{2})/.exec(value)?.[1] ?? value;
}

export function times(leg: Leg): string {
  return `${clock(leg.departs)} – ${clock(leg.arrives)}`;
}

export function legText(leg: Leg): string {
  return `${leg.from.code} → ${leg.to.code} · ${times(leg)}`;
}

export function collectPoints(card: PlanCard, pickedHotelId: string): MapPoint[] {
  const points: MapPoint[] = [];
  for (const hotel of card.hotels) {
    if (hotel.location) {
      points.push({
        key: `hotel-${hotel.id}`,
        kind: 'hotel',
        description: `Hotel: ${hotel.summary}`,
        badge: 'H',
        lat: hotel.location.lat,
        lon: hotel.location.lon,
        highlighted: hotel.id === pickedHotelId,
      });
    }
  }
  for (const [dayIndex, day] of card.days.entries()) {
    for (const [stopIndex, stop] of day.stops.entries()) {
      points.push({
        key: `stop-${dayIndex}-${stopIndex}`,
        kind: 'stop',
        description: `Day ${dayIndex + 1} stop ${stopIndex + 1}: ${stop.name}`,
        badge: `${dayIndex + 1}.${stopIndex + 1}`,
        lat: stop.lat,
        lon: stop.lon,
        highlighted: false,
      });
    }
  }
  for (const pin of card.reviewPins) {
    points.push({
      key: `${pin.kind}-${pin.name}`,
      kind: pin.kind,
      description: `${pin.kind === 'restaurant' ? 'Restaurant' : 'Place'}: ${pin.name}`,
      badge: pin.kind === 'restaurant' ? 'R' : 'P',
      lat: pin.lat,
      lon: pin.lon,
      highlighted: false,
    });
  }
  return points;
}

export function collectRoutes(legs: Leg[]): Route[] {
  const routes: Route[] = [];
  for (const [index, leg] of legs.entries()) {
    const { from, to } = leg;
    if (from.lat !== null && from.lon !== null && to.lat !== null && to.lon !== null) {
      routes.push({
        key: `leg-${index}`,
        from: { lat: from.lat, lng: from.lon },
        to: { lat: to.lat, lng: to.lon },
        middle: { lat: (from.lat + to.lat) / 2, lng: (from.lon + to.lon) / 2 },
        times: times(leg),
      });
    }
  }
  return routes;
}

export function boundsOf(coordinates: LatLng[]) {
  const lats = coordinates.map((coordinate) => coordinate.lat);
  const lngs = coordinates.map((coordinate) => coordinate.lng);
  let north = Math.max(...lats);
  let south = Math.min(...lats);
  let east = Math.max(...lngs);
  let west = Math.min(...lngs);
  if (north === south) {
    north += 0.02;
    south -= 0.02;
  }
  if (east === west) {
    east += 0.02;
    west -= 0.02;
  }
  return { north, south, east, west };
}

export type Edges = ReturnType<typeof boundsOf>;

export function legsGeoJson(routes: Route[]) {
  return {
    type: 'FeatureCollection' as const,
    features: routes.map((route) => ({
      type: 'Feature' as const,
      properties: {},
      geometry: {
        type: 'LineString' as const,
        coordinates: [
          [route.from.lng, route.from.lat],
          [route.to.lng, route.to.lat],
        ],
      },
    })),
  };
}

export function colorOf(kind: PointKind): string {
  if (kind === 'hotel') {
    return HOTEL_COLOR;
  }
  return kind === 'stop' ? STOP_COLOR : REVIEW_COLOR;
}
