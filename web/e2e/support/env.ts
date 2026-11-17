import { test } from '@playwright/test';

export const BASE_URL = process.env.E2E_BASE_URL ?? 'http://localhost:8080';
export const APP_HOST = new URL(BASE_URL).host;

export const REPLY_TIMEOUT_MS = 300_000;
export const SIGN_IN_TIMEOUT_MS = 240_000;
export const TRIP_TEST_TIMEOUT_MS = 720_000;
export const SINGLE_TEST_TIMEOUT_MS = 900_000;
export const CORRELATION_FAILURE = /correlation failed|unable to unprotect the message\.state/i;
export const DENIED_HEADING = 'Access is by invitation only';
export const MFA_PROMPT =
  /approve sign in request|verify your identity|enter code|authenticator app/i;
export const DEMO_REQUEST = '5 days in Tokyo, mid-budget, food and hiking';

export type Creds = { email: string; password: string };

export function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export function pause(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

export function isoIn(days: number): string {
  return new Date(Date.now() + days * 86_400_000).toISOString().slice(0, 10);
}

export function credsFrom(prefix: string): Creds | null {
  const email = process.env[`${prefix}_EMAIL`];
  const password = process.env[`${prefix}_PASSWORD`];
  return email && password ? { email, password } : null;
}

export function requireCreds(prefix: string): Creds {
  const creds = credsFrom(prefix);
  if (!creds) {
    test.skip(true, `${prefix}_EMAIL and ${prefix}_PASSWORD are not set in the test environment`);
    throw new Error('unreachable');
  }
  return creds;
}

export function missingModelCredentials(): string[] {
  const provider = (process.env.MODEL_PROVIDER ?? '').trim().toLowerCase() || 'openai_chat';
  const unset = (names: string[]): string[] =>
    names.filter((name) => (process.env[name] ?? '').trim() === '');
  switch (provider) {
    case 'anthropic':
      return unset(['ANTHROPIC_API_KEY']);
    case 'openai_chat':
      return unset(['OPENAI_API_KEY']);
    case 'azure_openai':
      return unset(['AZURE_OPENAI_ENDPOINT', 'AZURE_OPENAI_API_KEY', 'AZURE_OPENAI_API_VERSION']);
    default:
      return [`a supported MODEL_PROVIDER (got "${provider}")`];
  }
}
