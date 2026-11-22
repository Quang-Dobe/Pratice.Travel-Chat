import { z } from 'zod';

export const Me = z.object({
  id: z.string(),
  name: z.string(),
  email: z.string(),
});
export type Me = z.infer<typeof Me>;

export const SessionTimeLeft = z.object({
  secondsLeft: z.number().int().min(0),
  endsBy: z.enum(['idle', 'limit']),
});
export type SessionTimeLeft = z.infer<typeof SessionTimeLeft>;

export const ConversationItem = z.object({
  id: z.string(),
  title: z.string(),
  createdAt: z.string(),
});
export type ConversationItem = z.infer<typeof ConversationItem>;

export const ConversationList = z.object({ items: z.array(ConversationItem) });

function isHttpsUrl(value: string): boolean {
  try {
    return new URL(value).protocol === 'https:';
  } catch {
    return false;
  }
}

export const ToolStatus = z.enum(['started', 'finished']);
export type ToolStatus = z.infer<typeof ToolStatus>;

export const ToolCall = z.object({
  name: z.string(),
  kind: z.string(),
  status: ToolStatus.default('finished'),
});
export type ToolCall = z.infer<typeof ToolCall>;

export const NO_FORECAST = 'no forecast available yet';

const Latitude = z.number().min(-90).max(90);
const Longitude = z.number().min(-180).max(180);

export const Airport = z.object({
  code: z.string(),
  name: z.string(),
  lat: Latitude.nullable().default(null),
  lon: Longitude.nullable().default(null),
});
export type Airport = z.infer<typeof Airport>;

export const Leg = z.object({
  from: Airport,
  to: Airport,
  departs: z.string(),
  arrives: z.string(),
});
export type Leg = z.infer<typeof Leg>;

export const GeoPoint = z.object({ lat: Latitude, lon: Longitude });
export type GeoPoint = z.infer<typeof GeoPoint>;

export const Stop = z.object({ name: z.string(), lat: Latitude, lon: Longitude });
export type Stop = z.infer<typeof Stop>;

export const ReviewPin = z.object({
  name: z.string(),
  kind: z.enum(['restaurant', 'place']),
  lat: Latitude,
  lon: Longitude,
});
export type ReviewPin = z.infer<typeof ReviewPin>;

export const Option = z.object({
  id: z.string(),
  summary: z.string(),
  priceUsd: z.number(),
  deepLink: z.string().refine(isHttpsUrl, 'deepLink must be an https URL'),
  legs: z.array(Leg).default([]),
  location: GeoPoint.nullable().default(null),
});
export type Option = z.infer<typeof Option>;

export const Picked = z.object({ flightId: z.string(), hotelId: z.string() });
export type Picked = z.infer<typeof Picked>;

export const PlanDay = z.object({
  date: z.string(),
  activities: z.array(z.string()).default([]),
  weather: z.string().default(NO_FORECAST),
  costUsd: z.number().default(0),
  stops: z.array(Stop).default([]),
});
export type PlanDay = z.infer<typeof PlanDay>;

export const PlanBudget = z.object({
  flightUsd: z.number().default(0),
  hotelUsd: z.number().default(0),
  dailyUsd: z.number(),
  totalUsd: z.number(),
  budgetUsd: z.number().nullable(),
  overBudget: z.boolean(),
});
export type PlanBudget = z.infer<typeof PlanBudget>;

export const PlanCard = z.object({
  id: z.string(),
  status: z.enum(['open', 'approved', 'replaced']),
  flights: z.array(Option),
  hotels: z.array(Option),
  picked: Picked,
  days: z.array(PlanDay),
  budget: PlanBudget,
  reviewPins: z.array(ReviewPin).default([]),
});
export type PlanCard = z.infer<typeof PlanCard>;

export const Pill = z.object({
  text: z.string(),
  action: z.enum(['approve', 'reject']).nullable().default(null),
});
export type Pill = z.infer<typeof Pill>;

export type Action =
  | { type: 'approve'; cardId: string; flightId: string; hotelId: string }
  | { type: 'reject'; cardId: string };

const MAX_SOURCE_LINKS = 3;
const EXPORT_URL_PREFIX = '/api/conversations/';

export const SourceLink = z.object({ title: z.string(), url: z.string() });
export type SourceLink = z.infer<typeof SourceLink>;

export const SourceItem = z.object({
  subject: z.string(),
  links: z
    .array(SourceLink)
    .default([])
    .transform((links) => links.filter((link) => isHttpsUrl(link.url)).slice(0, MAX_SOURCE_LINKS)),
});
export type SourceItem = z.infer<typeof SourceItem>;

export const SourceList = z
  .array(SourceItem)
  .default([])
  .transform((items) => items.filter((item) => item.links.length > 0));

export const ArtifactItem = z.object({
  id: z.string(),
  format: z.enum(['html', 'docx', 'xlsx', 'csv']),
  fileName: z.string(),
  url: z
    .string()
    .refine((value) => value.startsWith(EXPORT_URL_PREFIX), 'url must be an export path'),
  draft: z.boolean().default(false),
});
export type ArtifactItem = z.infer<typeof ArtifactItem>;

const MessageSchema = z.object({
  role: z.enum(['user', 'assistant']),
  text: z.string(),
  toolCalls: z.array(ToolCall).default([]),
  planCard: PlanCard.nullable().default(null),
  pills: z.array(Pill).default([]),
  sources: SourceList,
  artifacts: z.array(ArtifactItem).default([]),
});
export type Message = Omit<z.infer<typeof MessageSchema>, 'sources' | 'artifacts'> & {
  sources?: SourceItem[];
  artifacts?: ArtifactItem[];
};

export type Conversation = ConversationItem & { messages: Message[] };
export const ConversationSchema = ConversationItem.extend({ messages: z.array(MessageSchema) });

export type AppData = {
  me: Me;
  conversations: ConversationItem[];
};
