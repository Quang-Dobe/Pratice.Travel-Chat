import { Alert, Box, Typography } from '@mui/material';
import {
  AdvancedMarker,
  APIProvider,
  Map as GoogleMap,
  Polyline,
  useMap,
} from '@vis.gl/react-google-maps';
import { Component, type ReactNode, useEffect, useState, useSyncExternalStore } from 'react';
import { getConfig, type Leg, type PlanCard } from '../api/api';

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

type GmWindow = { gm_authFailure: (() => void) | undefined };

let mapAuthFailed = false;
const authListeners = new Set<() => void>();

function subscribeAuthFailure(listener: () => void): () => void {
  authListeners.add(listener);
  return () => {
    authListeners.delete(listener);
  };
}

function readAuthFailure(): boolean {
  return mapAuthFailed;
}

function reportAuthFailure(): void {
  mapAuthFailed = true;
  for (const listener of [...authListeners]) {
    listener();
  }
}

if (typeof window !== 'undefined') {
  (window as unknown as GmWindow).gm_authFailure = reportAuthFailure;
}

export function resetMapAuthFailureForTests(): void {
  mapAuthFailed = false;
  for (const listener of [...authListeners]) {
    listener();
  }
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
  const map = useMap();
  useEffect(() => {
    map?.fitBounds({ north, south, east, west }, FIT_PADDING);
  }, [map, north, south, east, west]);
  return null;
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
  const [apiKey, setApiKey] = useState<string | null>(null);
  const [loadFailed, setFailed] = useState(false);
  const authFailed = useSyncExternalStore(subscribeAuthFailure, readAuthFailure, readAuthFailure);
  const failed = loadFailed || authFailed;
  const [zoom, setZoom] = useState<number | null>(null);

  const legs = card.flights.find((flight) => flight.id === pickedFlightId)?.legs ?? [];
  const points = collectPoints(card, pickedHotelId ?? card.picked.hotelId);
  const routes = collectRoutes(legs);
  const coordinates = [
    ...points.map((point) => ({ lat: point.lat, lng: point.lon })),
    ...routes.flatMap((route) => [route.from, route.to]),
  ];
  const hasCoordinates = coordinates.length > 0;
  const edges = hasCoordinates ? boundsOf(coordinates) : null;

  useEffect(() => {
    if (!hasCoordinates) {
      return undefined;
    }
    let active = true;
    void getConfig().then((config) => {
      if (active) {
        setApiKey(config.googleMapsApiKey);
      }
    });
    return () => {
      active = false;
    };
  }, [hasCoordinates]);

  function renderMap() {
    if (edges === null || apiKey === '' || failed) {
      return <Alert severity="info">The map is unavailable right now.</Alert>;
    }
    if (apiKey === null) {
      return null;
    }
    return (
      <MapBoundary>
        <Box
          component="section"
          aria-label="Trip map"
          data-zoom={zoom ?? undefined}
          sx={{ height: 320, borderRadius: 1, overflow: 'hidden' }}
        >
          <APIProvider apiKey={apiKey} onError={() => setFailed(true)}>
            <GoogleMap
              mapId="DEMO_MAP_ID"
              gestureHandling="cooperative"
              defaultBounds={{ ...edges, padding: FIT_PADDING }}
              onCameraChanged={(event) => setZoom(event.detail.zoom)}
              style={{ width: '100%', height: '100%' }}
            >
              <FitBounds {...edges} />
              {routes.map((route) => (
                <Polyline
                  key={route.key}
                  path={[route.from, route.to]}
                  strokeColor={FLIGHT_COLOR}
                  strokeWeight={3}
                />
              ))}
              {routes.map((route) => (
                <AdvancedMarker key={`${route.key}-times`} position={route.middle}>
                  <Badge text={route.times} color={FLIGHT_COLOR} highlighted={false} />
                </AdvancedMarker>
              ))}
              {points.map((point) => (
                <AdvancedMarker
                  key={point.key}
                  position={{ lat: point.lat, lng: point.lon }}
                  title={point.description}
                >
                  <Badge
                    text={point.badge}
                    color={colorOf(point.kind)}
                    highlighted={point.highlighted}
                  />
                </AdvancedMarker>
              ))}
            </GoogleMap>
          </APIProvider>
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
