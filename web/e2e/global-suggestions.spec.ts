import {
  type Browser,
  expect,
  type Locator,
  type Page,
  type Response as PageResponse,
  type Route,
  test,
} from '@playwright/test';
import { compose } from './global-setup';
import { asUser, signIn } from './support/auth';
import { conversationIdFrom } from './support/chat';
import { type Creds, requireCreds, SIGN_IN_TIMEOUT_MS } from './support/env';
import { historyLink, messageBox, replyText, userBubbles } from './support/locators';

const STARTERS_PATH = '/api/conversations/starters';
const SETTING_PATH = `${STARTERS_PATH}/setting`;
const HISTORY_PATH = `${STARTERS_PATH}/history`;
const API_TIMEOUT_MS = 30_000;
const UI_TIMEOUT_MS = 15_000;
const HANG_TIMEOUT_MS = 3_000;
const PSQL_TIMEOUT_MS = 60_000;
const TEST_TIMEOUT_MS = 2 * SIGN_IN_TIMEOUT_MS + 180_000;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const WORDS_PATTERN = /^[A-Za-z ]+$/;
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const MESSAGES_PATH = /^\/api\/conversations\/([0-9a-f-]{36})\/messages$/i;
const CONVERSATION_PATH = /^\/api\/conversations\/([0-9a-f-]{36})$/i;
const SWITCH_LABEL = 'Suggestions from my past trips';

type Trip = {
  destination: string;
  tripType: string;
  preferences: string[];
  startDate?: string;
  endDate?: string;
};

const heading = (page: Page): Locator => page.getByRole('heading', { name: /^Where to next/ });
const suggestionsGroup = (page: Page): Locator => page.getByRole('group', { name: 'Suggestions' });
const profileDialog = (page: Page): Locator => page.getByRole('dialog');
const suggestionsSwitch = (page: Page): Locator => profileDialog(page).getByLabel(SWITCH_LABEL);

const isStartersRead = (response: PageResponse): boolean =>
  response.request().method() === 'GET' && new URL(response.url()).pathname === STARTERS_PATH;
const isSettingWrite = (response: PageResponse): boolean =>
  response.request().method() === 'PUT' && new URL(response.url()).pathname === SETTING_PATH;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function uniqueTag(): string {
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`;
}

function nextMarch(): { startDate: string; endDate: string } {
  const now = new Date();
  const year = now.getUTCMonth() < 2 ? now.getUTCFullYear() : now.getUTCFullYear() + 1;
  return { startDate: `${year}-03-10`, endDate: `${year}-03-14` };
}

function sseBody(reply: string): string {
  const words = reply.split(' ');
  const tokens = words.map((word, index) => {
    const text = index === 0 ? word : ` ${word}`;
    return `event: token\ndata: ${JSON.stringify({ text })}\n\n`;
  });
  return `${tokens.join('')}event: done\ndata: {}\n\n`;
}

function quote(value: string): string {
  if (!WORDS_PATTERN.test(value)) {
    throw new Error(`Unsafe SQL text: ${value}`);
  }
  return `'${value}'`;
}

function dateLiteral(value: string | undefined): string {
  if (value === undefined) {
    return 'NULL';
  }
  if (!DATE_PATTERN.test(value)) {
    throw new Error(`Unsafe SQL date: ${value}`);
  }
  return `DATE '${value}'`;
}

function insertSummarySql(conversationId: string, trip: Trip): string {
  if (!UUID_PATTERN.test(conversationId)) {
    throw new Error(`Not a conversation id: ${conversationId}`);
  }
  const preferences =
    trip.preferences.length > 0
      ? `ARRAY[${trip.preferences.map(quote).join(',')}]::text[]`
      : 'ARRAY[]::text[]';
  return (
    'INSERT INTO trip_summaries ' +
    '(conversation_id, user_id, destination, start_date, end_date, trip_type, preferences) ' +
    `SELECT id, user_id, ${quote(trip.destination)}, ${dateLiteral(trip.startDate)}, ` +
    `${dateLiteral(trip.endDate)}, ${quote(trip.tripType)}, ${preferences} ` +
    `FROM conversations WHERE id = '${conversationId}'`
  );
}

function psql(sql: string): string {
  return compose(
    [
      'exec',
      '-T',
      'postgres',
      'psql',
      '-U',
      'travelchat',
      '-d',
      'qagent',
      '-v',
      'ON_ERROR_STOP=1',
      '-c',
      sql,
    ],
    PSQL_TIMEOUT_MS,
  );
}

function removeSeeded(conversationIds: string[]): void {
  const safe = conversationIds.filter((id) => UUID_PATTERN.test(id));
  if (safe.length === 0) {
    return;
  }
  const ids = safe.map((id) => `'${id}'`).join(',');
  try {
    psql(
      `DELETE FROM trip_summaries WHERE conversation_id IN (${ids}); ` +
        `DELETE FROM exports WHERE conversation_id IN (${ids}); ` +
        `DELETE FROM conversations WHERE id IN (${ids})`,
    );
  } catch {
    return;
  }
}

async function resetStarters(page: Page): Promise<void> {
  const on = await page.request.put(SETTING_PATH, {
    data: { enabled: true },
    timeout: API_TIMEOUT_MS,
  });
  expect(on.status()).toBe(200);
  const cleared = await page.request.delete(HISTORY_PATH, { timeout: API_TIMEOUT_MS });
  expect(cleared.status()).toBe(204);
}

async function restoreStarters(page: Page): Promise<void> {
  try {
    await resetStarters(page);
  } catch {
    return;
  }
}

async function createConversation(page: Page, label: string): Promise<string> {
  const response = await page.request.post('/api/conversations', {
    data: { text: `${label} ${uniqueTag()}` },
    timeout: API_TIMEOUT_MS,
  });
  expect(response.status()).toBe(201);
  const body: unknown = await response.json();
  if (!isRecord(body) || typeof body.id !== 'string') {
    throw new Error('The created conversation has no id');
  }
  return body.id;
}

async function seedTrip(page: Page, seeded: string[], trip: Trip): Promise<string> {
  const id = await createConversation(page, `Suggestions ${trip.destination}`);
  seeded.push(id);
  expect(psql(insertSummarySql(id, trip))).toContain('INSERT 0 1');
  return id;
}

async function withUserA(
  browser: Browser,
  run: (page: Page, seeded: string[]) => Promise<void>,
): Promise<void> {
  await asUser(browser, requireCreds('E2E_USER_A'), async (page) => {
    const seeded: string[] = [];
    await resetStarters(page);
    try {
      await run(page, seeded);
    } finally {
      removeSeeded(seeded);
      await restoreStarters(page);
    }
  });
}

async function afterRender(page: Page): Promise<void> {
  await page.evaluate(
    () =>
      new Promise<void>((resolve) => {
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
      }),
  );
}

type StartersBody = { enabled: boolean; items: string[] };

function parseStarters(body: unknown): StartersBody {
  if (!isRecord(body) || typeof body.enabled !== 'boolean' || !Array.isArray(body.items)) {
    throw new Error('Unexpected starters body');
  }
  const items = body.items.filter((item): item is string => typeof item === 'string');
  if (items.length !== body.items.length) {
    throw new Error('Unexpected starters items');
  }
  return { enabled: body.enabled, items };
}

async function openNewChat(page: Page): Promise<StartersBody> {
  const read = page.waitForResponse(isStartersRead, { timeout: API_TIMEOUT_MS });
  await page.goto('/');
  const response = await read;
  expect(response.status()).toBe(200);
  const body = parseStarters(await response.json());
  await expect(heading(page)).toBeVisible({ timeout: UI_TIMEOUT_MS });
  await afterRender(page);
  return body;
}

async function pillTexts(page: Page): Promise<string[]> {
  const group = suggestionsGroup(page);
  await expect(group).toBeVisible({ timeout: UI_TIMEOUT_MS });
  return group.getByRole('button').allInnerTexts();
}

async function openProfile(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Profile', exact: true }).click();
  await expect(profileDialog(page)).toBeVisible({ timeout: UI_TIMEOUT_MS });
}

async function closeProfile(page: Page): Promise<void> {
  await profileDialog(page).getByRole('button', { name: 'Close', exact: true }).click();
  await expect(profileDialog(page)).toBeHidden({ timeout: UI_TIMEOUT_MS });
}

async function flipSwitch(page: Page, enabled: boolean): Promise<void> {
  const toggle = suggestionsSwitch(page);
  await expect(toggle).toBeEnabled({ timeout: UI_TIMEOUT_MS });
  const written = page.waitForResponse(isSettingWrite, { timeout: API_TIMEOUT_MS });
  await toggle.click();
  expect((await written).status()).toBe(200);
  if (enabled) {
    await expect(toggle).toBeChecked({ timeout: UI_TIMEOUT_MS });
  } else {
    await expect(toggle).not.toBeChecked({ timeout: UI_TIMEOUT_MS });
  }
}

function stubMessages(
  page: Page,
  reply: string,
  sent: { id: string; text: string },
): Promise<void> {
  return page.route(
    (url) => MESSAGES_PATH.test(url.pathname),
    async (route: Route) => {
      const request = route.request();
      if (request.method() !== 'POST') {
        await route.fallback();
        return;
      }
      const match = MESSAGES_PATH.exec(new URL(request.url()).pathname);
      const body: unknown = request.postDataJSON();
      sent.id = match?.[1] ?? '';
      sent.text = isRecord(body) && typeof body.text === 'string' ? body.text : '';
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: sseBody(reply),
      });
    },
  );
}

function stubConversation(
  page: Page,
  reply: string,
  sent: { id: string; text: string },
): Promise<void> {
  return page.route(
    (url) => CONVERSATION_PATH.test(url.pathname),
    async (route: Route) => {
      const request = route.request();
      const id = CONVERSATION_PATH.exec(new URL(request.url()).pathname)?.[1];
      if (request.method() !== 'GET' || id === undefined || id !== sent.id) {
        await route.fallback();
        return;
      }
      await route.fulfill({
        status: 200,
        json: {
          id,
          title: 'Suggestions stub',
          createdAt: new Date().toISOString(),
          messages: [
            { role: 'user', text: sent.text },
            { role: 'assistant', text: reply },
          ],
        },
      });
    },
  );
}

test.describe('global-suggestions', () => {
  test.describe.configure({ timeout: TEST_TIMEOUT_MS });

  test(
    'E2E-1 [Happy Case] a past trip shows up as starter pills about it',
    { annotation: { type: 'covers', description: 'SC-1' } },
    async ({ browser }) => {
      await withUserA(browser, async (page, seeded) => {
        await seedTrip(page, seeded, {
          destination: 'Da Nang',
          tripType: 'beach',
          preferences: ['quiet hotels'],
          ...nextMarch(),
        });

        await openNewChat(page);

        const texts = await pillTexts(page);
        expect(texts.length).toBeGreaterThanOrEqual(1);
        expect(texts.length).toBeLessThanOrEqual(4);
        for (const text of texts) {
          expect(text).toContain('Da Nang');
        }
      });
    },
  );

  test(
    'E2E-2 [Happy Case] picking a starter pill starts a new chat with its text',
    { annotation: { type: 'covers', description: 'SC-2' } },
    async ({ browser }) => {
      const tag = uniqueTag();
      const reply = `Lovely idea, here is a start. Reply-end-${tag}`;
      await withUserA(browser, async (page, seeded) => {
        const seededId = await seedTrip(page, seeded, {
          destination: 'Da Nang',
          tripType: 'beach',
          preferences: [],
          ...nextMarch(),
        });
        const sent = { id: '', text: '' };
        await stubMessages(page, reply, sent);
        await stubConversation(page, reply, sent);
        await openNewChat(page);
        const pill = suggestionsGroup(page).getByRole('button').first();
        await expect(pill).toBeVisible({ timeout: UI_TIMEOUT_MS });
        const pillText = (await pill.innerText()).trim();

        await pill.click();

        await expect(page).toHaveURL(/\/c\/[0-9a-f-]{36}$/i, { timeout: API_TIMEOUT_MS });
        const openedId = conversationIdFrom(page);
        seeded.push(openedId);
        expect(openedId).not.toBe(seededId);
        expect(sent.text).toBe(pillText);
        await expect(userBubbles(page)).toHaveCount(1, { timeout: UI_TIMEOUT_MS });
        await expect(userBubbles(page).first()).toContainText(pillText);
        await expect(replyText(page)).toContainText(`Reply-end-${tag}`, {
          timeout: UI_TIMEOUT_MS,
        });
      });
    },
  );

  test(
    'E2E-3 [Happy Case] the setting is on for a user who never touched it',
    { annotation: { type: 'covers', description: 'SC-3' } },
    async ({ browser }) => {
      await asUser(browser, requireCreds('E2E_USER_B'), async (page) => {
        await expect(messageBox(page)).toBeVisible({ timeout: UI_TIMEOUT_MS });

        await openProfile(page);

        await expect(suggestionsSwitch(page)).toBeChecked({ timeout: UI_TIMEOUT_MS });
      });
    },
  );

  test(
    'E2E-4 [Happy Case] turning the setting off and back on',
    { annotation: { type: 'covers', description: 'SC-4' } },
    async ({ browser }) => {
      await withUserA(browser, async (page, seeded) => {
        await seedTrip(page, seeded, {
          destination: 'Da Nang',
          tripType: 'beach',
          preferences: [],
        });
        await openNewChat(page);
        await pillTexts(page);

        await openProfile(page);
        await expect(suggestionsSwitch(page)).toBeChecked({ timeout: UI_TIMEOUT_MS });
        await flipSwitch(page, false);
        await closeProfile(page);
        const off = await openNewChat(page);

        expect(off).toEqual({ enabled: false, items: [] });
        await expect(suggestionsGroup(page)).toHaveCount(0);
        await expect(page.getByText('Food and hiking in Tokyo')).toBeVisible();

        await openProfile(page);
        await flipSwitch(page, true);
        await closeProfile(page);
        await openNewChat(page);

        const texts = await pillTexts(page);
        expect(texts.length).toBeGreaterThanOrEqual(1);
      });
    },
  );

  test(
    'E2E-5 [Happy Case] clearing suggestion history keeps every conversation',
    { annotation: { type: 'covers', description: 'SC-5' } },
    async ({ browser }) => {
      await withUserA(browser, async (page, seeded) => {
        const ids: string[] = [];
        for (const destination of ['Da Nang', 'Hanoi', 'Hue']) {
          const trip = { destination, tripType: 'food', preferences: [] };
          ids.push(await seedTrip(page, seeded, trip));
        }
        await openNewChat(page);
        await pillTexts(page);

        await openProfile(page);
        await profileDialog(page)
          .getByRole('button', { name: 'Clear suggestion history', exact: true })
          .click();
        await expect(profileDialog(page).getByText('Suggestion history cleared.')).toBeVisible({
          timeout: UI_TIMEOUT_MS,
        });
        await closeProfile(page);
        const body = await openNewChat(page);

        expect(body).toEqual({ enabled: true, items: [] });
        await expect(suggestionsGroup(page)).toHaveCount(0);
        for (const id of ids) {
          await historyLink(page, id).click();
          await expect(page).toHaveURL(new RegExp(`/c/${id}$`), { timeout: UI_TIMEOUT_MS });
          await expect(messageBox(page)).toBeVisible({ timeout: UI_TIMEOUT_MS });
          await expect(page.getByText('Conversation not found')).toHaveCount(0);
        }
      });
    },
  );

  test(
    'E2E-7 [Happy Case] the setting survives signing out and in',
    { annotation: { type: 'covers', description: 'SC-7' } },
    async ({ browser }) => {
      const creds: Creds = requireCreds('E2E_USER_A');
      await asUser(browser, creds, async (page) => {
        await resetStarters(page);
        try {
          await page.goto('/');
          await expect(messageBox(page)).toBeVisible({ timeout: UI_TIMEOUT_MS });
          await openProfile(page);
          await flipSwitch(page, false);
          await closeProfile(page);

          await page.getByRole('button', { name: 'Logout', exact: true }).click();
          await expect(page).toHaveURL(/\/login$/, { timeout: API_TIMEOUT_MS });
          await page.context().clearCookies();
          expect(await signIn(page, creds)).toBe('chat');

          await openProfile(page);
          await expect(suggestionsSwitch(page)).toBeEnabled({ timeout: UI_TIMEOUT_MS });
          await expect(suggestionsSwitch(page)).not.toBeChecked({ timeout: UI_TIMEOUT_MS });
        } finally {
          await restoreStarters(page);
        }
      });
    },
  );

  test(
    'E2E-9 another user sees no starter pills from user A trips',
    {
      annotation: {
        type: 'covers',
        description: 'Constraint: suggestions come only from the signed-in user own conversations',
      },
    },
    async ({ browser }) => {
      await withUserA(browser, async (pageA, seeded) => {
        await seedTrip(pageA, seeded, {
          destination: 'Da Nang',
          tripType: 'beach',
          preferences: [],
        });
        await openNewChat(pageA);
        expect((await pillTexts(pageA)).length).toBeGreaterThanOrEqual(1);

        await asUser(browser, requireCreds('E2E_USER_B'), async (pageB) => {
          const cleared = await pageB.request.delete(HISTORY_PATH, { timeout: API_TIMEOUT_MS });
          expect(cleared.status()).toBe(204);

          const body = await openNewChat(pageB);

          expect(body.items).toEqual([]);
          expect(body.items.some((item) => item.includes('Da Nang'))).toBe(false);
          await expect(suggestionsGroup(pageB)).toHaveCount(0);
        });
      });
    },
  );

  test(
    'E2E-12 the new-chat screen never waits on broken or slow suggestions',
    {
      annotation: {
        type: 'covers',
        description: 'Constraint: if suggestions cannot be built the screen looks as it does today',
      },
    },
    async ({ browser }) => {
      await withUserA(browser, async (page, seeded) => {
        await seedTrip(page, seeded, {
          destination: 'Da Nang',
          tripType: 'beach',
          preferences: [],
        });
        let release: () => void = () => undefined;
        const held = new Promise<void>((resolve) => {
          release = resolve;
        });
        let mode: 'abort' | 'hold' = 'abort';
        let hits = 0;
        await page.route(
          (url) => url.pathname === STARTERS_PATH,
          async (route: Route) => {
            if (route.request().method() !== 'GET') {
              await route.fallback();
              return;
            }
            hits += 1;
            if (mode === 'hold') {
              await held;
            }
            await route.abort('failed').catch(() => undefined);
          },
        );
        try {
          await page.goto('/');
          await expect(heading(page)).toBeVisible({ timeout: UI_TIMEOUT_MS });
          await expect(page.getByText('Food and hiking in Tokyo')).toBeVisible();
          await messageBox(page).fill('Typing while suggestions failed');
          await expect(messageBox(page)).toHaveValue('Typing while suggestions failed');
          await afterRender(page);
          await expect(suggestionsGroup(page)).toHaveCount(0);
          expect(hits).toBeGreaterThanOrEqual(1);

          mode = 'hold';
          const hitsBefore = hits;
          await page.goto('/');
          await expect(heading(page)).toBeVisible({ timeout: HANG_TIMEOUT_MS });
          await expect(page.getByRole('status', { name: 'Loading suggestions' })).toBeVisible();
          await expect(page.getByText('Food and hiking in Tokyo')).toBeVisible({
            timeout: HANG_TIMEOUT_MS,
          });
          await messageBox(page).fill('Typing while suggestions hang');
          await expect(messageBox(page)).toHaveValue('Typing while suggestions hang', {
            timeout: HANG_TIMEOUT_MS,
          });
          await expect.poll(() => hits, { timeout: HANG_TIMEOUT_MS }).toBeGreaterThan(hitsBefore);
          await expect(suggestionsGroup(page)).toHaveCount(0);
        } finally {
          release();
        }
      });
    },
  );
});
