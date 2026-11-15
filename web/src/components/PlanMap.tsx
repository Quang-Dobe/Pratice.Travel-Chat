import { Alert, Box, Typography } from '@mui/material';
import 'maplibre-gl/dist/maplibre-gl.css';
import { Component, type ReactNode, useEffect, useState } from 'react';
import {
  Layer,
  Map as MapView,
  Marker,
  NavigationControl,
  Source,
  useMap,
} from 'react-map-gl/maplibre';
import type { Leg, PlanCard } from '../api/api';

const HOTEL_COLOR = '#1565c0';
const STOP_COLOR = '#2e7d32';
const REVIEW_COLOR = '#ef6c00';
const FLIGHT_COLOR = '#455a64';
const FIT_PADDING = 32;

const visuallyHidden = {
  border: 0,
  clip: 'rect(0 0 0 0)',
  height: '1px',
  margin: '-1px',
  overflow: 'hidden',
  padding: 0,
  position: 'absolute',
  whiteSpace: 'nowrap',
  width: '1px',
} as const;

type PointKind = 'hotel' | 'stop' | 'restaurant' | 'place';

type MapPoint = {
  key: string;
  kind: PointKind;
  description: string;
  badge: string;
  lat: number;
  lon: number;
  highlighted: boolean;
};

type LatLng = { lat: number; lng: number };

type Route = {
  key: string;
  from: LatLng;
  to: LatLng;
  middle: LatLng;
  times: string;
};

function styleUrl(): string {
  return `${window.location.origin}/api/maps/v1/styles/osm-bright/style.json`;
}

type MapBoundaryProps = { children: ReactNode };

class MapBoundary extends Component<MapBoundaryProps, { crashed: boolean }> {
  state = { crashed: false };

  static getDerivedStateFromError() {
    return { crashed: true };
  }

  render() {
    if (this.state.crashed) {
      return <Alert severity="info">The map is unavailable right now.</Alert>;
    }
    return this.props.children;
  }
}

function clock(value: string): string {
  return /[T ](\d{2}:\d{2})/.exec(value)?.[1] ?? value;
}

function times(leg: Leg): string {
  return `${clock(leg.departs)} – ${clock(leg.arrives)}`;
}

function legText(leg: Leg): string {
  return `${leg.from.code} → ${leg.to.code} · ${times(leg)}`;
}

function collectPoints(card: PlanCard, pickedHotelId: string): MapPoint[] {
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

function collectRoutes(legs: Leg[]): Route[] {
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

function boundsOf(coordinates: LatLng[]) {
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

type Edges = ReturnType<typeof boundsOf>;

function FitBounds({ north, south, east, west }: Edges) {
  const { current: map } = useMap();
  useEffect(() => {
    map?.fitBounds(
      [
        [west, south],
        [east, north],
      ],
      { padding: FIT_PADDING },
    );
  }, [map, north, south, east, west]);
  return null;
}

function legsGeoJson(routes: Route[]) {
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

function colorOf(kind: PointKind): string {
  if (kind === 'hotel') {
    return HOTEL_COLOR;
  }
  return kind === 'stop' ? STOP_COLOR : REVIEW_COLOR;
}

type BadgeProps = { text: string; color: string; highlighted: boolean };

function Badge({ text, color, highlighted }: BadgeProps) {
  return (
    <Box
      component="span"
      aria-hidden="true"
      sx={{
        display: 'inline-block',
        minWidth: highlighted ? 28 : 22,
        px: 0.5,
        py: highlighted ? 0.5 : 0.25,
        borderRadius: 2,
        bgcolor: color,
        color: '#fff',
        fontSize: 12,
        fontWeight: 700,
        textAlign: 'center',
        border: highlighted ? '3px solid #fff' : '1px solid #fff',
        boxShadow: highlighted ? '0 0 0 2px #0d47a1' : 'none',
      }}
    >
      {text}
    </Box>
  );
}

type PlanMapProps = {
  card: PlanCard;
  pickedFlightId: string;
  pickedHotelId?: string;
};

export function PlanMap({ card, pickedFlightId, pickedHotelId }: PlanMapProps) {
  const [failed, setFailed] = useState(false);
  const [zoom, setZoom] = useState<number | null>(null);

  const legs = card.flights.find((flight) => flight.id === pickedFlightId)?.legs ?? [];
  const points = collectPoints(card, pickedHotelId ?? card.picked.hotelId);
  const routes = collectRoutes(legs);
  const coordinates = [
    ...points.map((point) => ({ lat: point.lat, lng: point.lon })),
    ...routes.flatMap((route) => [route.from, route.to]),
  ];
  const edges = coordinates.length > 0 ? boundsOf(coordinates) : null;

  function renderMap() {
    if (edges === null || failed) {
      return <Alert severity="info">The map is unavailable right now.</Alert>;
    }
    return (
      <MapBoundary>
        <Box
          component="section"
          aria-label="Trip map"
          data-zoom={zoom ?? undefined}
          sx={{ height: 320, borderRadius: 1, overflow: 'hidden' }}
        >
          <MapView
            mapStyle={styleUrl()}
            initialViewState={{
              bounds: [
                [edges.west, edges.south],
                [edges.east, edges.north],
              ],
              fitBoundsOptions: { padding: FIT_PADDING },
            }}
            onMove={(event) => setZoom(event.viewState.zoom)}
            onError={() => setFailed(true)}
            style={{ width: '100%', height: '100%' }}
          >
            <NavigationControl showCompass={false} />
            <FitBounds {...edges} />
            <Source type="geojson" data={legsGeoJson(routes)}>
              <Layer type="line" paint={{ 'line-color': FLIGHT_COLOR, 'line-width': 3 }} />
            </Source>
            {routes.map((route) => (
              <Marker
                key={`${route.key}-times`}
                longitude={route.middle.lng}
                latitude={route.middle.lat}
              >
                <Badge text={route.times} color={FLIGHT_COLOR} highlighted={false} />
              </Marker>
            ))}
            {points.map((point) => (
              <Marker key={point.key} longitude={point.lon} latitude={point.lat}>
                <span title={point.description}>
                  <Badge
                    text={point.badge}
                    color={colorOf(point.kind)}
                    highlighted={point.highlighted}
                  />
                </span>
              </Marker>
            ))}
          </MapView>
        </Box>
      </MapBoundary>
    );
  }

  return (
    <Box>
      {renderMap()}
      {legs.length > 0 && (
        <Box component="ul" aria-label="Flight legs" sx={{ my: 0.5, pl: 2.5 }}>
          {legs.map((leg, index) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: legs have no id
            <Typography key={index} variant="body2" component="li">
              {legText(leg)}
            </Typography>
          ))}
        </Box>
      )}
      {points.length > 0 && (
        <Box component="ul" aria-label="Map points" sx={visuallyHidden}>
          {points.map((point) => (
            <li key={point.key}>{point.description}</li>
          ))}
        </Box>
      )}
    </Box>
  );
}
