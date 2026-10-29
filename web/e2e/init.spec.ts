import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { type Browser, expect, type Locator, type Page, test } from '@playwright/test';
import { compose, REPO_ROOT } from './global-setup';

const BASE_URL = process.env.E2E_BASE_URL ?? 'http://localhost:8080';
const APP_HOST = new URL(BASE_URL).host;
const USERS_FILE = join(REPO_ROOT, 'users.json');
const ENV_EXAMPLE_FILE = join(REPO_ROOT, '.env.example');

const LEGACY_PLACEHOLDER = /placeholder reply|planner is not connected yet/i;
const REPLY_TIMEOUT_MS = 180_000;
const SERVICES = ['postgres', 'redis', 'q-user', 'q-gateway', 'q-agent', 'q-mcp', 'q-query', 'web'];
const DENIED_HEADING = 'Access is by invitation only';
const CORRELATION_FAILURE = /correlation failed|unable to unprotect the message\.state/i;
const FIRST_SIGN_IN_PROMPT =
  /update your password|more information required|action required|keep your account secure/i;
const MFA_PROMPT = /approve sign in request|verify your identity|enter code|authenticator app/i;

const DEFAULT_TIMEOUT_MS = 300_000;
const COMPOSE_TIMEOUT_MS = 1_800_000;
const SIGN_IN_TIMEOUT_MS = 240_000;

test.describe.configure({ timeout: DEFAULT_TIMEOUT_MS });

type Creds = { email: string; password: string };
type InvitedUser = { oid: string; name: string; email: string };
type Outcome = 'chat' | 'denied' | 'correlation' | 'entra' | 'pending';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function pause(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function uniqueTag(): string {
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`;
}

function credsFrom(prefix: string): Creds | null {
  const email = process.env[`${prefix}_EMAIL`];
  const password = process.env[`${prefix}_PASSWORD`];
  return email && password ? { email, password } : null;
}

function requireCreds(prefix: string): Creds {
  const creds = credsFrom(prefix);
  if (!creds) {
    test.skip(true, `${prefix}_EMAIL and ${prefix}_PASSWORD are not set in the test environment`);
    throw new Error('unreachable');
  }
  return creds;
}

function parseUsers(raw: string): InvitedUser[] {
  const parsed: unknown = JSON.parse(raw);
  if (!Array.isArray(parsed)) {
    throw new Error('users.json is not an array');
  }
  return parsed.map((entry: unknown) => {
    if (
      !isRecord(entry) ||
      typeof entry.oid !== 'string' ||
      typeof entry.name !== 'string' ||
      typeof entry.email !== 'string'
    ) {
      throw new Error('users.json entry is not {oid, name, email}');
    }
    return { oid: entry.oid, name: entry.name, email: entry.email };
  });
}

function parsePsOutput(raw: string): unknown[] {
  const trimmed = raw.trim();
  if (!trimmed) {
    return [];
  }
  if (trimmed.startsWith('[')) {
    const parsed: unknown = JSON.parse(trimmed);
    return Array.isArray(parsed) ? parsed : [];
  }
  return trimmed.split('\n').map((line) => JSON.parse(line) as unknown);
}

function serviceHealth(): Map<string, string> {
  const health = new Map<string, string>();
  for (const entry of parsePsOutput(compose(['ps', '--all', '--format', 'json']))) {
    if (isRecord(entry) && typeof entry.Service === 'string') {
      health.set(entry.Service, String(entry.Health || entry.State));
    }
  }
  return health;
}

function unhealthyServices(): string[] {
  const health = serviceHealth();
  return SERVICES.filter((service) => health.get(service) !== 'healthy').map(
    (service) => `${service}=${health.get(service) ?? 'missing'}`,
  );
}

function activeUserEmails(): string[] {
  const output = compose([
    'exec',
    '-T',
    'postgres',
    'psql',
    '-U',
    'travelchat',
    '-d',
    'quser',
    '-tA',
    '-c',
    'select email from users where is_active order by email',
  ]);
  return output
    .split('\n')
    .map((line) => line.trim().toLowerCase())
    .filter((line) => line !== '')
    .sort();
}

const newConversationLink = (page: Page): Locator =>
  page.getByRole('link', { name: 'New conversation' });
const historyItems = (page: Page): Locator =>
  page.getByRole('list', { name: 'Conversation history' }).locator('a');
const conversationLog = (page: Page): Locator => page.getByRole('log', { name: 'Conversation' });
const turnBubbles = (page: Page): Locator => conversationLog(page).locator(':scope > div');
const assistantBubbles = (page: Page): Locator =>
  turnBubbles(page).filter({ has: page.getByText('Assistant', { exact: true }) });
const messageBox = (page: Page): Locator => page.getByRole('textbox', { name: 'Message' });
const historyLink = (page: Page, id: string): Locator =>
  page.getByRole('list', { name: 'Conversation history' }).locator(`a[href="/c/${id}"]`);
const replyText = (page: Page): Locator => assistantBubbles(page).last().locator('p').nth(1);

async function bodyText(page: Page): Promise<string> {
  return page
    .locator('body')
    .innerText({ timeout: 2_000 })
    .catch(() => '');
}

async function readOutcome(page: Page): Promise<Outcome> {
  try {
    if (new URL(page.url()).host !== APP_HOST) {
      return 'entra';
    }
    if (await newConversationLink(page).isVisible()) {
      return 'chat';
    }
    if (await page.getByRole('heading', { name: DENIED_HEADING }).isVisible()) {
      return 'denied';
    }
    return CORRELATION_FAILURE.test(await bodyText(page)) ? 'correlation' : 'pending';
  } catch {
    return 'pending';
  }
}

async function correlationError(page: Page): Promise<Error> {
  const cookies = (await page.context().cookies())
    .map((cookie) => `${cookie.name}[secure=${cookie.secure},sameSite=${cookie.sameSite}]`)
    .join(', ');
  const url = new URL(page.url());
  return new Error(
    `OIDC "Correlation failed" at ${url.pathname}. The correlation/nonce cookies set on ` +
      `${BASE_URL} were not sent back on the /signin-oidc redirect. Plain HTTP with ` +
      `SameSite=None or Secure cookies is the usual cause; check the cookie policy in q-gateway. ` +
      `Cookies in the browser (names only): ${cookies || 'none'}`,
  );
}

async function advanceEntra(page: Page, creds: Creds, done: Set<string>): Promise<boolean> {
  const emailInput = page
    .locator(
      'input[type="email"]:not([aria-hidden="true"]), input[name="loginfmt"]:not([aria-hidden="true"])',
    )
    .first();
  const passwordInput = page
    .locator(
      'input[type="password"]:not([aria-hidden="true"]), input[name="passwd"]:not([aria-hidden="true"])',
    )
    .first();
  const picker = page.getByRole('button', { name: new RegExp(escapeRegExp(creds.email), 'i') });
  if (!done.has('email') && (await emailInput.isVisible())) {
    done.add('email');
    await emailInput.fill(creds.email);
    await page.getByRole('button', { name: 'Next' }).click();
    return false;
  }
  if (!done.has('password') && (await passwordInput.isVisible())) {
    done.add('password');
    await passwordInput.fill(creds.password);
    await page.getByRole('button', { name: 'Sign in' }).click();
    return false;
  }
  if (!done.has('picker') && (await picker.first().isVisible())) {
    done.add('picker');
    await picker.first().click();
    return false;
  }
  if (await page.getByText('Stay signed in?').isVisible()) {
    await page.getByRole('button', { name: 'No' }).click();
    return false;
  }
  return MFA_PROMPT.test(await bodyText(page));
}

async function signIn(page: Page, creds: Creds): Promise<'chat' | 'denied'> {
  await page.goto('/login');
  await page.getByRole('button', { name: 'Sign in' }).click();
  const done = new Set<string>();
  const deadline = Date.now() + SIGN_IN_TIMEOUT_MS;
  let mfaSeen = false;
  while (Date.now() < deadline) {
    const outcome = await readOutcome(page);
    if (outcome === 'chat' || outcome === 'denied') {
      return outcome;
    }
    if (outcome === 'correlation') {
      throw await correlationError(page);
    }
    if (outcome === 'entra') {
      if (FIRST_SIGN_IN_PROMPT.test(await bodyText(page))) {
        throw new Error(
          `Account ${creds.email} must finish first sign-in by hand (password change / MFA setup), then update web/.env.e2e`,
        );
      }
      const mfa = await advanceEntra(page, creds, done).catch(() => false);
      mfaSeen = mfaSeen || mfa;
    }
    await pause(500);
  }
  const url = new URL(page.url());
  const hint = mfaSeen
    ? ' An MFA prompt was shown: run headed (pnpm e2e --headed) and approve it by hand.'
    : '';
  throw new Error(
    `Sign-in did not finish within ${SIGN_IN_TIMEOUT_MS / 1000}s; stuck at ${url.host}${url.pathname}.${hint}`,
  );
}

async function inSession<T>(browser: Browser, run: (page: Page) => Promise<T>): Promise<T> {
  const context = await browser.newContext({ baseURL: BASE_URL });
  try {
    return await run(await context.newPage());
  } finally {
    await context.close();
  }
}

async function asUser<T>(
  browser: Browser,
  creds: Creds,
  run: (page: Page) => Promise<T>,
): Promise<T> {
  return inSession(browser, async (page) => {
    const outcome = await signIn(page, creds);
    if (outcome !== 'chat') {
      throw new Error(
        `${creds.email} was turned away as uninvited; its oid must be listed in users.json`,
      );
    }
    return run(page);
  });
}

async function sendMessage(page: Page, text: string): Promise<void> {
  await messageBox(page).fill(text);
  await page.getByRole('button', { name: 'Send' }).click();
}

async function expectReplies(page: Page, count: number): Promise<void> {
  await expect(assistantBubbles(page)).toHaveCount(count);
  await expect(messageBox(page)).toBeEnabled({ timeout: REPLY_TIMEOUT_MS });
  await expect(replyText(page)).not.toHaveText(/^\s*$/);
}

function conversationIdFrom(page: Page): string {
  const match = /\/c\/([^/?#]+)/.exec(page.url());
  if (!match?.[1]) {
    throw new Error(`Not on a conversation route: ${page.url()}`);
  }
  return match[1];
}

async function startNewConversation(page: Page): Promise<void> {
  await newConversationLink(page).click();
  await expect(assistantBubbles(page)).toHaveCount(0);
}

async function createConversation(page: Page, firstMessage: string): Promise<string> {
  await startNewConversation(page);
  await sendMessage(page, firstMessage);
  await expectReplies(page, 1);
  await expect(page).toHaveURL(/\/c\/[^/]+$/);
  return conversationIdFrom(page);
}

async function logout(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Logout' }).click();
  await expect(page).toHaveURL(/\/login$/);
}

async function langsmithRootRunCount(conversationId: string): Promise<number> {
  const key = process.env.LANGSMITH_API_KEY ?? '';
  const project = process.env.LANGSMITH_PROJECT ?? '';
  const base = process.env.LANGSMITH_ENDPOINT ?? 'https://api.smith.langchain.com';
  const headers = { 'x-api-key': key, 'Content-Type': 'application/json' };

  const sessionsResponse = await fetch(
    `${base}/api/v1/sessions?name=${encodeURIComponent(project)}`,
    { headers },
  );
  if (!sessionsResponse.ok) {
    throw new Error(`Langsmith sessions lookup returned ${sessionsResponse.status}`);
  }
  const sessions: unknown = await sessionsResponse.json();
  const session = Array.isArray(sessions) ? sessions[0] : undefined;
  if (!isRecord(session) || typeof session.id !== 'string') {
    throw new Error(`Langsmith project "${project}" was not found`);
  }

  const runsResponse = await fetch(`${base}/api/v1/runs/query`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      session: [session.id],
      is_root: true,
      limit: 20,
      filter: `and(eq(metadata_key, "conversation_id"), eq(metadata_value, "${conversationId}"))`,
    }),
  });
  if (!runsResponse.ok) {
    throw new Error(`Langsmith runs query returned ${runsResponse.status}`);
  }
  const body: unknown = await runsResponse.json();
  return isRecord(body) && Array.isArray(body.runs) ? body.runs.length : 0;
}

function missingModelCredentials(): string[] {
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

const CREDENTIAL_NAME = /^(ANTHROPIC|OPENAI|AZURE_OPENAI)_(API_KEY|BASE_URL|ENDPOINT)$/;

function buildEnvFileFromExample(): { dir: string; file: string } {
  const keys = readFileSync(ENV_EXAMPLE_FILE, 'utf8')
    .split(/\r?\n/)
    .map((line) => line.split('=')[0]?.trim() ?? '')
    .filter((key) => key !== '' && !key.startsWith('#'));
  const dir = mkdtempSync(join(tmpdir(), 'e2e-env-'));
  const file = join(dir, 'e2e.env');
  const lines = keys.map(
    (key) => `${key}=${CREDENTIAL_NAME.test(key) ? '' : (process.env[key] ?? '')}`,
  );
  writeFileSync(file, `${lines.join('\n')}\n`);
  return { dir, file };
}

test.describe('init', () => {
  test(
    'E2E-1 [Happy Case] fresh local start brings everything up',
    { annotation: { type: 'covers', description: 'SC-1' } },
    async () => {
      test.setTimeout(COMPOSE_TIMEOUT_MS);
      const expected = parseUsers(readFileSync(USERS_FILE, 'utf8'))
        .map((user) => user.email.toLowerCase())
        .sort();
      for (const prefix of ['E2E_USER_A', 'E2E_USER_B']) {
        const creds = credsFrom(prefix);
        if (creds) {
          expect(expected, `users.json must list ${prefix}`).toContain(creds.email.toLowerCase());
        }
      }

      compose(['down', '-v']);
      compose(['up', '-d', '--build', '--wait', '--wait-timeout', '600']);

      await expect
        .poll(() => unhealthyServices(), { timeout: 120_000, intervals: [3_000] })
        .toEqual([]);
      await expect
        .poll(() => activeUserEmails(), { timeout: 60_000, intervals: [2_000] })
        .toEqual(expected);
    },
  );

  test(
    'E2E-2 [Happy Case] invited user signs in and reaches chat',
    { annotation: { type: 'covers', description: 'SC-2' } },
    async ({ browser }) => {
      const creds = requireCreds('E2E_USER_A');
      await asUser(browser, creds, async (page) => {
        await expect(newConversationLink(page)).toBeVisible();
        await expect(page.getByRole('list', { name: 'Conversation history' })).toBeVisible();
        await expect(page.getByRole('button', { name: 'Profile' })).toBeVisible();
        await expect(page.getByRole('button', { name: 'Logout' })).toBeVisible();
      });
    },
  );

  test(
    'E2E-3 [Happy Case] new conversation gets a streamed reply',
    { annotation: { type: 'covers', description: 'SC-3' } },
    async ({ browser }) => {
      const creds = requireCreds('E2E_USER_A');
      await asUser(browser, creds, async (page) => {
        await startNewConversation(page);
        const client = await page.context().newCDPSession(page);
        await client.send('Network.enable');
        let requestId: string | null = null;
        let chunks = 0;
        let finish: () => void = () => undefined;
        const finished = new Promise<void>((resolve) => {
          finish = resolve;
        });
        client.on('Network.requestWillBeSent', (event) => {
          const { method, url } = event.request;
          const isMessagePost =
            method === 'POST' &&
            /\/api\/conversations\/[^/]+\/messages$/.test(new URL(url).pathname);
          if (isMessagePost) {
            requestId = event.requestId;
          }
        });
        let ending = 'still open';
        client.on('Network.dataReceived', (event) => {
          if (event.requestId === requestId && event.dataLength > 0) {
            chunks += 1;
          }
        });
        client.on('Network.loadingFinished', (event) => {
          if (event.requestId === requestId) {
            ending = 'finished';
            finish();
          }
        });
        client.on('Network.loadingFailed', (event) => {
          if (event.requestId === requestId) {
            ending = `failed: ${event.errorText}${event.canceled ? ' (canceled)' : ''}`;
            finish();
          }
        });

        await sendMessage(page, `Stream check ${uniqueTag()}`);
        await expectReplies(page, 1);
        await Promise.race([finished, pause(30_000)]);
        await client.detach().catch(() => undefined);

        expect(requestId, 'The messages POST was not observed').not.toBeNull();
        expect(
          chunks,
          `The reply bytes arrived in ${chunks} chunk(s) (request ${ending}). The gateway is ` +
            'likely buffering the SSE response: check YARP response flushing and the ' +
            'text/event-stream pass-through.',
        ).toBeGreaterThan(1);
      });
    },
  );

  test(
    'E2E-4 [Happy Case] new conversation shows in history, newest first',
    { annotation: { type: 'covers', description: 'SC-4' } },
    async ({ browser }) => {
      const creds = requireCreds('E2E_USER_A');
      await asUser(browser, creds, async (page) => {
        const older = `Older trip ${uniqueTag()}`;
        const newer = `Newer trip ${uniqueTag()}`;
        const olderId = await createConversation(page, older);
        const newerId = await createConversation(page, newer);

        await expect(historyItems(page).first()).toHaveAttribute('href', `/c/${newerId}`);
        await expect(historyItems(page).nth(1)).toHaveAttribute('href', `/c/${olderId}`);
      });
    },
  );

  test(
    'E2E-5 [Happy Case] conversation survives logout and sign-in',
    { annotation: { type: 'covers', description: 'SC-5' } },
    async ({ browser }) => {
      const creds = requireCreds('E2E_USER_A');
      await asUser(browser, creds, async (page) => {
        const text = `Persist me ${uniqueTag()}`;
        const id = await createConversation(page, text);
        await logout(page);

        expect(await signIn(page, creds)).toBe('chat');
        await historyLink(page, id).click();

        await expect(turnBubbles(page)).toHaveCount(2);
        await expect(turnBubbles(page)).toContainText([text, /\S/]);
      });
    },
  );

  test(
    'E2E-6 [Happy Case] continuing a reopened conversation saves the new turn',
    { annotation: { type: 'covers', description: 'SC-6' } },
    async ({ browser }) => {
      const creds = requireCreds('E2E_USER_A');
      await asUser(browser, creds, async (page) => {
        const first = `First turn ${uniqueTag()}`;
        const second = `Second turn ${uniqueTag()}`;
        const id = await createConversation(page, first);
        await startNewConversation(page);
        await historyLink(page, id).click();
        await expect(turnBubbles(page)).toHaveCount(2);

        await sendMessage(page, second);
        await expectReplies(page, 2);

        await page.reload();
        await newConversationLink(page).click();
        await historyLink(page, id).click();

        await expect(turnBubbles(page)).toHaveCount(4);
        await expect(turnBubbles(page)).toContainText([first, /\S/, second, /\S/]);
      });
    },
  );

  test(
    'E2E-7 [Happy Case] profile popup shows name and email',
    { annotation: { type: 'covers', description: 'SC-7' } },
    async ({ browser }) => {
      const creds = requireCreds('E2E_USER_A');
      const listed = parseUsers(readFileSync(USERS_FILE, 'utf8')).find(
        (user) => user.email.toLowerCase() === creds.email.toLowerCase(),
      );
      if (!listed) {
        test.skip(true, 'E2E_USER_A_EMAIL is not listed in users.json');
        return;
      }
      await asUser(browser, creds, async (page) => {
        await page.getByRole('button', { name: 'Profile' }).click();
        const dialog = page.getByRole('dialog', { name: 'Profile' });
        await expect(dialog).toContainText(listed.name);
        await expect(dialog).toContainText(new RegExp(escapeRegExp(listed.email), 'i'));
      });
    },
  );

  test(
    'E2E-8 [Happy Case] logout returns to sign-in',
    { annotation: { type: 'covers', description: 'SC-8' } },
    async ({ browser }) => {
      const creds = requireCreds('E2E_USER_A');
      await asUser(browser, creds, async (page) => {
        await logout(page);
        await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible();

        await page.goto('/');
        await expect(page).toHaveURL(/\/login$/);
        await expect(newConversationLink(page)).toBeHidden();
        const me = await page.request.get('/api/users/me');
        expect(me.status()).toBe(401);
      });
    },
  );

  test(
    'E2E-9 [Happy Case] each turn appears as a trace',
    { annotation: { type: 'covers', description: 'SC-9' } },
    async ({ browser }) => {
      const creds = requireCreds('E2E_USER_A');
      await asUser(browser, creds, async (page) => {
        const conversationId = await createConversation(page, `Trace one ${uniqueTag()}`);
        await sendMessage(page, `Trace two ${uniqueTag()}`);
        await expectReplies(page, 2);

        const canQuery = Boolean(process.env.LANGSMITH_API_KEY && process.env.LANGSMITH_PROJECT);
        if (!canQuery) {
          test.info().annotations.push({
            type: 'manual-check',
            description:
              `LANGSMITH_API_KEY/LANGSMITH_PROJECT not in the test environment. In Langsmith, ` +
              `confirm two chat-turn traces with metadata conversation_id=${conversationId}.`,
          });
          return;
        }
        await expect
          .poll(() => langsmithRootRunCount(conversationId), {
            timeout: 120_000,
            intervals: [3_000],
            message: `expected two Langsmith traces for conversation ${conversationId}`,
          })
          .toBe(2);
      });
    },
  );

  test(
    'E2E-10 uninvited work account is turned away',
    { annotation: { type: 'covers', description: 'Constraint: uninvited account turned away' } },
    async ({ browser }) => {
      const creds = requireCreds('E2E_UNINVITED');
      await inSession(browser, async (page) => {
        expect(await signIn(page, creds)).toBe('denied');
        await expect(page.getByRole('heading', { name: DENIED_HEADING })).toBeVisible();
        await expect(newConversationLink(page)).toBeHidden();

        await page.goto('/');
        await expect(newConversationLink(page)).toBeHidden();
      });
    },
  );

  test(
    'E2E-11 newly invited user gets in after restart',
    {
      annotation: { type: 'covers', description: 'Constraint: invite = edit users.json + restart' },
    },
    async ({ browser }) => {
      test.setTimeout(COMPOSE_TIMEOUT_MS);
      const creds = requireCreds('E2E_UNINVITED');
      const oid = process.env.E2E_UNINVITED_OID;
      if (!oid) {
        test.skip(
          true,
          'E2E_UNINVITED_OID (the Entra object id of the uninvited account) is not set',
        );
        return;
      }
      const original = readFileSync(USERS_FILE, 'utf8');
      const users = parseUsers(original);
      if (users.some((user) => user.email.toLowerCase() === creds.email.toLowerCase())) {
        test.skip(true, 'E2E_UNINVITED_EMAIL is already listed in users.json');
        return;
      }
      const invited: InvitedUser = {
        oid,
        name: process.env.E2E_UNINVITED_NAME ?? 'E2E Invited User',
        email: creds.email,
      };

      try {
        writeFileSync(USERS_FILE, `${JSON.stringify([...users, invited], null, 2)}\n`);
        compose(['up', '-d', '--force-recreate', '--wait', '--wait-timeout', '300', 'q-user']);
        await asUser(browser, creds, async (page) => {
          await expect(newConversationLink(page)).toBeVisible();
          await expect(page.getByRole('button', { name: 'Profile' })).toBeVisible();
        });
      } finally {
        writeFileSync(USERS_FILE, original);
        try {
          compose(['up', '-d', '--force-recreate', '--wait', '--wait-timeout', '300', 'q-user']);
        } catch (error) {
          test.info().annotations.push({
            type: 'cleanup-failed',
            description: `users.json restored on disk but q-user was not recreated: ${String(error)}`,
          });
        }
      }
    },
  );

  test(
    'E2E-12 users see only their own conversations',
    {
      annotation: {
        type: 'covers',
        description: 'Constraint: users see only their own conversations',
      },
    },
    async ({ browser }) => {
      const credsA = requireCreds('E2E_USER_A');
      const credsB = requireCreds('E2E_USER_B');
      const text = `Private to A ${uniqueTag()}`;
      const conversationId = await asUser(browser, credsA, (page) =>
        createConversation(page, text),
      );

      await asUser(browser, credsB, async (page) => {
        await expect(historyItems(page)).toHaveCount(0);

        await page.goto(`/c/${conversationId}`);
        await expect(page.getByRole('heading', { name: 'Conversation not found' })).toBeVisible();
        await expect(page.getByText(text)).toHaveCount(0);
      });
    },
  );

  test(
    'E2E-13 starts with no model credentials',
    {
      annotation: { type: 'covers', description: 'Constraint: starts without model credentials' },
    },
    async ({ browser }) => {
      test.setTimeout(COMPOSE_TIMEOUT_MS);
      if (missingModelCredentials().length === 0) {
        test.skip(
          true,
          'The configured model provider has its credentials set in ../.env, so a credential-free stack cannot be built from the test environment',
        );
        return;
      }
      const creds = requireCreds('E2E_USER_A');
      const missing = ['AzureAd__TenantId', 'AzureAd__ClientId', 'AzureAd__ClientSecret'].filter(
        (key) => !process.env[key],
      );
      if (missing.length > 0) {
        test.skip(
          true,
          `${missing.join(', ')} must be in the test environment to build the env file`,
        );
        return;
      }

      const exampleCredentialValues = readFileSync(ENV_EXAMPLE_FILE, 'utf8')
        .split(/\r?\n/)
        .filter((line) => CREDENTIAL_NAME.test(line.split('=')[0] ?? ''))
        .map((line) => line.slice(line.indexOf('=') + 1).trim());
      expect(exampleCredentialValues.length).toBeGreaterThan(0);
      expect(exampleCredentialValues, '.env.example must list the names with empty values').toEqual(
        exampleCredentialValues.map(() => ''),
      );

      const { dir, file } = buildEnvFileFromExample();
      try {
        compose([
          '--env-file',
          file,
          'up',
          '-d',
          '--force-recreate',
          '--wait',
          '--wait-timeout',
          '600',
        ]);
        await expect
          .poll(() => unhealthyServices(), { timeout: 120_000, intervals: [3_000] })
          .toEqual([]);

        const filledCredentialNames = compose(['exec', '-T', 'q-agent', 'env'])
          .split(/\r?\n/)
          .map((line) => {
            const separator = line.indexOf('=');
            return { name: line.slice(0, separator), value: line.slice(separator + 1).trim() };
          })
          .filter(({ name, value }) => CREDENTIAL_NAME.test(name.toUpperCase()) && value !== '')
          .map(({ name }) => name);
        expect(filledCredentialNames, 'q-agent must hold no model credential values').toEqual([]);

        await asUser(browser, creds, async (page) => {
          await startNewConversation(page);
          await sendMessage(page, `No claude ${uniqueTag()}`);
          await expect(assistantBubbles(page)).toHaveCount(1);
          await expect(assistantBubbles(page).last().getByRole('alert')).toContainText(
            /not configured/i,
            { timeout: REPLY_TIMEOUT_MS },
          );
          await expect(messageBox(page)).toBeEnabled();
        });
      } finally {
        rmSync(dir, { recursive: true, force: true });
        try {
          compose(['up', '-d', '--force-recreate', '--wait', '--wait-timeout', '600']);
        } catch (error) {
          test.info().annotations.push({
            type: 'cleanup-failed',
            description: `stack was not restored to the default .env: ${String(error)}`,
          });
        }
      }
    },
  );

  test(
    'E2E-14 travel question no longer gets the placeholder',
    { annotation: { type: 'covers', description: 'Out of scope: any real answer content' } },
    async ({ browser }) => {
      const creds = requireCreds('E2E_USER_A');
      await asUser(browser, creds, async (page) => {
        await startNewConversation(page);
        await sendMessage(page, 'What is the weather and which flights should I take to Tokyo?');
        await expectReplies(page, 1);

        expect(await replyText(page).innerText()).not.toMatch(LEGACY_PLACEHOLDER);
      });
    },
  );

  test(
    'E2E-15 long first message gives a shortened title',
    { annotation: { type: 'covers', description: 'In scope: shortened conversation title' } },
    async ({ browser }) => {
      const creds = requireCreds('E2E_USER_A');
      await asUser(browser, creds, async (page) => {
        const long = `${uniqueTag()} ${'alpha beta gamma delta epsilon '.repeat(8).trim()}`;
        const id = await createConversation(page, long);

        await expect(historyItems(page).first()).toHaveAttribute('href', `/c/${id}`);
        const title = (await historyItems(page).first().innerText()).trim();
        expect(title).not.toBe(long);
        expect(title.length).toBeGreaterThan(0);
        expect(title.length).toBeLessThanOrEqual(51);
      });
    },
  );
});
