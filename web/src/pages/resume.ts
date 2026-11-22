import { z } from 'zod';

const RESUME_KEY = 'travelchat.resume';

const Resume = z.object({
  userId: z.string(),
  conversationId: z.string().nullable(),
  draft: z.string(),
});
export type Resume = z.infer<typeof Resume>;

function removeRecord() {
  try {
    localStorage.removeItem(RESUME_KEY);
  } catch {
    return;
  }
}

function peekRecord(): Resume | null {
  try {
    const raw = localStorage.getItem(RESUME_KEY);
    if (raw === null) {
      return null;
    }
    const parsed = Resume.safeParse(JSON.parse(raw));
    return parsed.success ? parsed.data : null;
  } catch {
    return null;
  }
}

export function saveResume(record: Resume) {
  const existing = peekRecord();
  if (
    existing !== null &&
    existing.userId === record.userId &&
    existing.draft !== '' &&
    record.draft === ''
  ) {
    return;
  }
  try {
    localStorage.setItem(RESUME_KEY, JSON.stringify(record));
  } catch {
    return;
  }
}

export function readResume(userId: string): Resume | null {
  let raw: string | null;
  try {
    raw = localStorage.getItem(RESUME_KEY);
  } catch {
    return null;
  }
  if (raw === null) {
    return null;
  }
  let parsed: Resume;
  try {
    parsed = Resume.parse(JSON.parse(raw));
  } catch {
    removeRecord();
    return null;
  }
  if (parsed.userId !== userId) {
    removeRecord();
    return null;
  }
  return parsed;
}

export function clearResume() {
  removeRecord();
}
