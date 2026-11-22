import {
  type Browser,
  type BrowserContext,
  expect,
  type Page,
  type Request as PageRequest,
  type Response as PageResponse,
  test,
} from '@playwright/test';
import { compose } from './global-setup';
import { signedInPage, signIn } from './support/auth';
import { sendAndSettle } from './support/chat';
import {
  BASE_URL,
  type Creds,
  escapeRegExp,
  missingModelCredentials,
  REPLY_TIMEOUT_MS,
  requireCreds,
  SIGN_IN_TIMEOUT_MS,
  SINGLE_TEST_TIMEOUT_MS,
} from './support/env';
import { assistantBubbles, messageBox, replyText, userBubbles } from './support/locators';

const SECOND_MS = 1000;
const MINUTE_MS = 60 * SECOND_MS;
const HOUR_MS = 60 * MINUTE_MS;
const IDLE_MS = 30 * MINUTE_MS;
const LIMIT_MS = 8 * HOUR_MS;
const WARNING_TIMEOUT_MS = 28 * MINUTE_MS;
const GATEWAY_SERVICE = 'q-gateway';
const COMPOSE_STEP_TIMEOUT_MS = 120_000;
const APP_ORIGIN = new URL(BASE_URL).origin;
const MESSAGES_PATH = /^\/api\/conversations\/[^/]+\/messages$/;

const warningText = (page: Page) => page.getByText(/your session ends in 2 minutes/i);
const stayButton = (page: Page) => page.getByRole('button', { name: 'Stay signed in' });

const isApiCall = (url: URL): boolean =>
  url.origin === APP_ORIGIN && url.pathname.startsWith('/api/');
const isTimeLeftRead = (response: PageResponse): boolean =>
  response.request().method() === 'GET' && new URL(response.url()).pathname === '/api/session';
const isMeCall = (response: PageResponse): boolean =>
  new URL(response.url()).pathname === '/api/users/me';
const isMessagePostRequest = (request: PageRequest): boolean =>
  request.method() === 'POST' && MESSAGES_PATH.test(new URL(request.url()).pathname);

function sseBody(reply: string): string {
  const words = reply.split(' ');
  const tokens = words.map((word, index) => {
    const text = index === 0 ? word : ` ${word}`;
    return `event: token\ndata: ${JSON.stringify({ text })}\n\n`;
  });
  return `${tokens.join('')}event: done\ndata: {}\n\n`;
}

function requireModel(): void {
  const missing = missingModelCredentials();
  test.skip(missing.length > 0, `needs ${missing.join(', ')} in .env for the configured provider`);
}

function uniqueTag(): string {
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

class ServerSessionModel {
  private now = 0;
  private lastActivity = 0;
  private signedInAt = 0;

  reset(): void {
    this.signedInAt = this.now;
    this.lastActivity = this.now;
  }

  ended(): boolean {
    return this.now >= this.lastActivity + IDLE_MS || this.now >= this.signedInAt + LIMIT_MS;
  }

  timeLeft(): { secondsLeft: number; endsBy: 'idle' | 'limit' } {
    const idleEnd = this.lastActivity + IDLE_MS;
    const limitEnd = this.signedInAt + LIMIT_MS;
    const endsBy = limitEnd <= idleEnd ? 'limit' : 'idle';
    const end = endsBy === 'limit' ? limitEnd : idleEnd;
    return { secondsLeft: Math.max(0, Math.floor((end - this.now) / SECOND_MS)), endsBy };
  }

  async attach(context: BrowserContext): Promise<void> {
    await context.route(isApiCall, async (route) => {
      const request = route.request();
      if (this.ended()) {
        await route.fulfill({
          status: 401,
          contentType: 'application/problem+json',
          body: JSON.stringify({ status: 401 }),
        });
        return;
      }
      if (request.method() === 'GET' && new URL(request.url()).pathname === '/api/session') {
        await route.fulfill({ status: 200, json: this.timeLeft() });
        return;
      }
      this.lastActivity = this.now;
      await route.continue();
    });
  }

  async advance(context: BrowserContext, milliseconds: number): Promise<void> {
    this.now += milliseconds;
    await context.clock.fastForward(milliseconds);
  }
}

type Session = {
  context: BrowserContext;
  page: Page;
  model: ServerSessionModel;
  conversationId: string;
};

async function createEmptyConversation(context: BrowserContext): Promise<string> {
  const response = await context.request.post('/api/conversations', {
    data: { text: `Session timeout ${uniqueTag()}` },
  });
  expect(response.status()).toBe(201);
  const body: unknown = await response.json();
  if (!isRecord(body) || typeof body.id !== 'string') {
    throw new Error('The created conversation has no id');
  }
  return body.id;
}

function firstPageOf(context: BrowserContext): Page {
  const page = context.pages()[0];
  if (!page) {
    throw new Error('The signed-in page is missing');
  }
  return page;
}

async function openSession(browser: Browser, creds: Creds): Promise<Session> {
  const context = await signedInPage(browser, creds);
  try {
    const page = firstPageOf(context);
    const model = new ServerSessionModel();
    await model.attach(context);
    await context.clock.install({ time: new Date() });
    const conversationId = await createEmptyConversation(context);
    await page.goto(`/c/${conversationId}`);
    await expect(messageBox(page)).toBeVisible();
    return { context, page, model, conversationId };
  } catch (error) {
    await context.close();
    throw error;
  }
}

async function withSession(
  browser: Browser,
  creds: Creds,
  run: (session: Session) => Promise<void>,
): Promise<void> {
  const session = await openSession(browser, creds);
  try {
    await run(session);
  } finally {
    await session.context.close();
  }
}

async function signInAgainAs(session: Session, creds: Creds): Promise<void> {
  session.model.reset();
  await session.context.clearCookies();
  await session.context.clock.setSystemTime(new Date());
  expect(await signIn(session.page, creds)).toBe('chat');
}

async function expectOnConversation(page: Page, conversationId: string): Promise<void> {
  await expect(page).toHaveURL(new RegExp(`/c/${escapeRegExp(conversationId)}$`));
}

function restartGateway(): void {
  compose(['restart', GATEWAY_SERVICE], COMPOSE_STEP_TIMEOUT_MS);
  compose(
    ['up', '-d', '--wait', '--wait-timeout', '100', GATEWAY_SERVICE],
    COMPOSE_STEP_TIMEOUT_MS,
  );
}

test.describe('session-timeout', () => {
  test.describe.configure({ timeout: SINGLE_TEST_TIMEOUT_MS });

  test(
    'E2E-2 [Happy Case] warning appears two minutes before the idle timeout',
    { annotation: { type: 'covers', description: 'SC-2' } },
    async ({ browser }) => {
      await withSession(browser, requireCreds('E2E_USER_A'), async ({ context, page, model }) => {
        await expect(warningText(page)).toHaveCount(0);

        await model.advance(context, WARNING_TIMEOUT_MS);

        await expect(warningText(page)).toBeVisible();
        await expect(stayButton(page)).toBeVisible();
      });
    },
  );

  test(
    'E2E-3 [Happy Case] stay signed in renews the session',
    { annotation: { type: 'covers', description: 'SC-3' } },
    async ({ browser }) => {
      await withSession(
        browser,
        requireCreds('E2E_USER_A'),
        async ({ context, page, model, conversationId }) => {
          await model.advance(context, WARNING_TIMEOUT_MS);
          await expect(stayButton(page)).toBeVisible();

          const meCall = page.waitForResponse(isMeCall);
          await stayButton(page).click();
          expect((await meCall).status()).toBe(200);
          await expect(warningText(page)).toHaveCount(0);

          await model.advance(context, 27 * MINUTE_MS);

          await expect(warningText(page)).toHaveCount(0);
          await expect(messageBox(page)).toBeVisible();
          await expectOnConversation(page, conversationId);
          expect((await page.request.get('/api/users/me')).status()).toBe(200);
        },
      );
    },
  );

  test(
    'E2E-4 [Happy Case] idle timeout returns the user to the same conversation',
    { annotation: { type: 'covers', description: 'SC-4' } },
    async ({ browser }) => {
      const creds = requireCreds('E2E_USER_A');
      await withSession(browser, creds, async (session) => {
        await session.model.advance(session.context, IDLE_MS);
        await expect(session.page).toHaveURL(/\/login$/);

        await signInAgainAs(session, creds);

        await expectOnConversation(session.page, session.conversationId);
        await expect(messageBox(session.page)).toBeVisible();
      });
    },
  );

  test(
    'E2E-5 [Happy Case] unsent message comes back after signing in again',
    { annotation: { type: 'covers', description: 'SC-5' } },
    async ({ browser }) => {
      const creds = requireCreds('E2E_USER_A');
      const draft = `Unsent note ${uniqueTag()}`;
      await withSession(browser, creds, async (session) => {
        await messageBox(session.page).fill(draft);

        await session.model.advance(session.context, IDLE_MS);
        await expect(session.page).toHaveURL(/\/login$/);
        await signInAgainAs(session, creds);

        await expectOnConversation(session.page, session.conversationId);
        await expect(messageBox(session.page)).toHaveValue(draft);
      });
    },
  );

  test(
    'E2E-6 [Happy Case] streaming reply finishes and is kept',
    { annotation: { type: 'covers', description: 'SC-6' } },
    async ({ browser }) => {
      test.setTimeout(2 * SIGN_IN_TIMEOUT_MS + 120_000);
      const creds = requireCreds('E2E_USER_A');
      const tag = uniqueTag();
      const prompt = `Plan a relaxed day in Tokyo ${tag}`;
      const reply = `A relaxed Tokyo day starts with a slow walk. Reply-end-${tag}`;
      let release: () => void = () => undefined;
      const held = new Promise<void>((resolve) => {
        release = resolve;
      });
      await withSession(browser, creds, async (session) => {
        const { context, page, model, conversationId } = session;

        await page.route(
          (url) => url.origin === APP_ORIGIN && MESSAGES_PATH.test(url.pathname),
          async (route) => {
            if (route.request().method() !== 'POST') {
              await route.fallback();
              return;
            }
            await held;
            await route.fulfill({
              status: 200,
              contentType: 'text/event-stream',
              body: sseBody(reply),
            });
          },
        );
        await page.evaluate((marker) => {
          const holder = window as unknown as { __replyShown?: boolean };
          holder.__replyShown = false;
          new MutationObserver(() => {
            if (document.body.textContent?.includes(marker)) {
              holder.__replyShown = true;
            }
          }).observe(document.body, { subtree: true, childList: true, characterData: true });
        }, `Reply-end-${tag}`);

        const sent = page.waitForRequest(isMessagePostRequest, { timeout: 30_000 });
        await messageBox(page).fill(prompt);
        await page.getByRole('button', { name: 'Send', exact: true }).click();
        await sent;
        await expect(assistantBubbles(page)).toHaveCount(1);
        await expect(messageBox(page)).toBeDisabled();

        const reading = page.waitForResponse(isTimeLeftRead, { timeout: 60_000 });
        await model.advance(context, IDLE_MS);
        const endedReading = await reading;
        if (endedReading.status() !== 401) {
          expect(await endedReading.json()).toMatchObject({ secondsLeft: 0 });
        }
        await expect(messageBox(page)).toBeDisabled();
        await expect(page).not.toHaveURL(/\/login$/);

        release();
        await expect(page).toHaveURL(/\/login$/, { timeout: 30_000 });
        const replyShown = await page.evaluate(
          () => (window as unknown as { __replyShown?: boolean }).__replyShown,
        );
        expect(replyShown, 'the stubbed reply never finished on screen').toBe(true);

        const conversationPath = `/api/conversations/${conversationId}`;
        await page.route(
          (url) => url.origin === APP_ORIGIN && url.pathname === conversationPath,
          async (route) => {
            if (route.request().method() !== 'GET') {
              await route.fallback();
              return;
            }
            await route.fulfill({
              status: 200,
              json: {
                id: conversationId,
                title: 'Session timeout stub',
                createdAt: new Date().toISOString(),
                messages: [
                  { role: 'user', text: prompt },
                  { role: 'assistant', text: reply },
                ],
              },
            });
          },
        );
        await signInAgainAs(session, creds);

        await expectOnConversation(page, conversationId);
        await expect(userBubbles(page), 'stub-only: from the routed conversation GET').toHaveCount(
          1,
        );
        await expect(
          assistantBubbles(page),
          'stub-only: from the routed conversation GET',
        ).toHaveCount(1);
        await expect(replyText(page), 'stub-only: from the routed conversation GET').toContainText(
          `Reply-end-${tag}`,
        );
      }).finally(() => release());
    },
  );

  test(
    'E2E-7 [Happy Case] gateway restart signs no one out',
    { annotation: { type: 'covers', description: 'SC-7' } },
    async ({ browser }) => {
      test.setTimeout(SIGN_IN_TIMEOUT_MS + 600_000 + REPLY_TIMEOUT_MS);
      requireModel();
      const context = await signedInPage(browser, requireCreds('E2E_USER_A'));
      try {
        const page = firstPageOf(context);
        const conversationId = await createEmptyConversation(context);
        await page.goto(`/c/${conversationId}`);
        await expect(messageBox(page)).toBeVisible();

        restartGateway();
        const meCall = page.waitForResponse(isMeCall, { timeout: REPLY_TIMEOUT_MS + 30_000 });
        void meCall.catch(() => undefined);
        await sendAndSettle(page, 'Suggest one good breakfast spot in Tokyo, in a sentence.');

        await expect(page).not.toHaveURL(/\/login$/);
        await expectOnConversation(page, conversationId);
        await expect(replyText(page)).not.toHaveText(/^\s*$/);
        expect((await meCall).status()).toBe(200);

        const reading = page.waitForResponse(isTimeLeftRead, { timeout: 30_000 });
        await page.reload();
        const timeLeft = await reading;
        expect(timeLeft.status()).toBe(200);
        expect(await timeLeft.json()).toMatchObject({ endsBy: 'idle' });
        await expectOnConversation(page, conversationId);
        await expect(messageBox(page)).toBeVisible();
      } finally {
        await context.close();
      }
    },
  );

  test(
    'E2E-9 activity in one tab keeps another tab signed in',
    {
      annotation: {
        type: 'covers',
        description:
          'Constraint: activity is any signed-in API call from any tab, except time-left',
      },
    },
    async ({ browser }) => {
      await withSession(
        browser,
        requireCreds('E2E_USER_A'),
        async ({ context, page, model, conversationId }) => {
          const second = await context.newPage();
          await second.goto(`/c/${conversationId}`);
          await expect(messageBox(second)).toBeVisible();

          await model.advance(context, 20 * MINUTE_MS);
          await second.reload();
          await expect(messageBox(second)).toBeVisible();

          const reading = page.waitForResponse(isTimeLeftRead);
          await model.advance(context, 8 * MINUTE_MS);
          const reading28 = await (await reading).json();
          expect(reading28).toMatchObject({ endsBy: 'idle' });
          expect(reading28.secondsLeft).toBeGreaterThanOrEqual(1300);
          expect(reading28.secondsLeft).toBeLessThanOrEqual(1320);

          await expect(warningText(page)).toHaveCount(0);
          await expectOnConversation(page, conversationId);
          await expect(messageBox(page)).toBeVisible();
        },
      );
    },
  );

  test(
    'E2E-13 a kept message is not shown to another person',
    {
      annotation: {
        type: 'covers',
        description:
          'Constraint: a kept message is restored only for the same person in the same conversation',
      },
    },
    async ({ browser }) => {
      const credsA = requireCreds('E2E_USER_A');
      const credsB = requireCreds('E2E_USER_B');
      const secret = `Private note from A ${uniqueTag()}`;
      await withSession(browser, credsA, async (session) => {
        const { page, conversationId } = session;
        await messageBox(page).fill(secret);
        await session.model.advance(session.context, IDLE_MS);
        await expect(page).toHaveURL(/\/login$/);

        await signInAgainAs(session, credsB);

        await expect(messageBox(page)).toBeVisible();
        await expect(messageBox(page)).toHaveValue('');
        await expect(page.getByText(secret)).toHaveCount(0);
        await expect(page).not.toHaveURL(new RegExp(escapeRegExp(conversationId)));
        const stored = await page.evaluate(() =>
          Object.keys(window.localStorage).map((key) => window.localStorage.getItem(key) ?? ''),
        );
        expect(stored.join('\n')).not.toContain(secret);
      });
    },
  );
});
