import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  type Browser,
  type BrowserContext,
  expect,
  type Locator,
  type Page,
  test,
} from '@playwright/test';
import { compose, REPO_ROOT } from './global-setup';

const BASE_URL = process.env.E2E_BASE_URL ?? 'http://localhost:8080';
const APP_HOST = new URL(BASE_URL).host;

const REPLY_TIMEOUT_MS = 300_000;
const SIGN_IN_TIMEOUT_MS = 240_000;
const TRIP_TEST_TIMEOUT_MS = 720_000;
const SINGLE_TEST_TIMEOUT_MS = 900_000;
const LINK_TIMEOUT_MS = 45_000;
const NO_FORECAST = 'no forecast available yet';
const CORRELATION_FAILURE = /correlation failed|unable to unprotect the message\.state/i;
const DENIED_HEADING = 'Access is by invitation only';
const FIRST_SIGN_IN_PROMPT =
  /update your password|more information required|action required|keep your account secure/i;
const MFA_PROMPT = /approve sign in request|verify your identity|enter code|authenticator app/i;
const DEMO_REQUEST = '5 days in Tokyo, mid-budget, food and hiking';
const MONEY_PATTERN = /\$[\d,]+\.\d{2}/;

type Creds = { email: string; password: string };
type Outcome = 'chat' | 'denied' | 'correlation' | 'entra' | 'pending';
type OptionGroupName = 'Flight options' | 'Hotel options';
type PillName = 'Approve' | 'Reject';
type CardSnapshot = { flights: string[]; hotels: string[]; activities: string[] };
type BudgetReading = {
  total: number;
  limit: number | null;
  flight: number;
  hotel: number;
  daily: number;
};

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function pause(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function isoIn(days: number): string {
  return new Date(Date.now() + days * 86_400_000).toISOString().slice(0, 10);
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

function requirePlannerEnv(): void {
  const missing = missingModelCredentials();
  test.skip(missing.length > 0, `needs ${missing.join(', ')} in .env for the configured provider`);
}

function requirePlanEnv(): void {
  requirePlannerEnv();
  test.skip(!process.env.SERPAPI_API_KEY, 'needs SERPAPI_API_KEY in .env for flights and hotels');
}

const newConversationLink = (page: Page): Locator =>
  page.getByRole('link', { name: 'New conversation' });
const historyItems = (page: Page): Locator =>
  page.getByRole('list', { name: 'Conversation history' }).locator('a');
const historyLink = (page: Page, id: string): Locator =>
  page.getByRole('list', { name: 'Conversation history' }).locator(`a[href="/c/${id}"]`);
const conversationLog = (page: Page): Locator => page.getByRole('log', { name: 'Conversation' });
const turnBubbles = (page: Page): Locator => conversationLog(page).locator(':scope > div');
const assistantBubbles = (page: Page): Locator =>
  turnBubbles(page).filter({ has: page.getByText('Assistant', { exact: true }) });
const userBubbles = (page: Page): Locator =>
  turnBubbles(page).filter({ has: page.getByText('You', { exact: true }) });
const messageBox = (page: Page): Locator => page.getByRole('textbox', { name: 'Message' });
const replyText = (page: Page): Locator => assistantBubbles(page).last().locator('p').nth(1);
const toolChips = (bubble: Locator): Locator =>
  bubble.getByRole('group', { name: 'Tool progress' });
const planCards = (page: Page): Locator =>
  conversationLog(page).getByRole('group', { name: 'Plan card' });
const latestCard = (page: Page): Locator => planCards(page).last();
const suggestionPills = (page: Page): Locator =>
  conversationLog(page).getByRole('group', { name: 'Suggestions' });
const pillButton = (page: Page, name: PillName): Locator =>
  suggestionPills(page).getByRole('button', { name, exact: true });
const optionGroup = (card: Locator, name: OptionGroupName): Locator =>
  card.getByRole('radiogroup', { name });
const optionRows = (group: Locator): Locator => group.locator(':scope > div');
const itineraryDays = (card: Locator): Locator =>
  card.getByRole('region', { name: /^Day \d{4}-\d{2}-\d{2}$/ });
const budgetRegion = (card: Locator): Locator => card.getByRole('region', { name: 'Budget' });
const statusChip = (card: Locator, label: 'Approved' | 'Replaced'): Locator =>
  card.getByText(label, { exact: true });

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
  const url = new URL(page.url());
  return new Error(
    `OIDC "Correlation failed" at ${url.pathname}. The correlation cookies set on ${BASE_URL} were not sent back on the /signin-oidc redirect.`,
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

async function signedInPage(browser: Browser, creds: Creds): Promise<BrowserContext> {
  const context = await browser.newContext({ baseURL: BASE_URL });
  try {
    const outcome = await signIn(await context.newPage(), creds);
    if (outcome !== 'chat') {
      throw new Error(`${creds.email} was turned away as uninvited`);
    }
    return context;
  } catch (error) {
    await context.close();
    throw error;
  }
}

async function asUser<T>(
  browser: Browser,
  creds: Creds,
  run: (page: Page) => Promise<T>,
): Promise<T> {
  const context = await signedInPage(browser, creds);
  try {
    const page = context.pages()[0];
    if (!page) {
      throw new Error('The signed-in page is missing');
    }
    return await run(page);
  } finally {
    await context.close();
  }
}

async function settled(page: Page): Promise<void> {
  await expect(messageBox(page)).toBeEnabled({ timeout: REPLY_TIMEOUT_MS });
}

async function sendAndSettle(page: Page, text: string): Promise<void> {
  const before = await assistantBubbles(page).count();
  await messageBox(page).fill(text);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(assistantBubbles(page)).toHaveCount(before + 1);
  await settled(page);
}

async function clickPillAndSettle(page: Page, name: PillName): Promise<void> {
  const before = await assistantBubbles(page).count();
  const pill = pillButton(page, name);
  await expect(pill).toBeEnabled({ timeout: REPLY_TIMEOUT_MS });
  await pill.click();
  await expect(assistantBubbles(page)).toHaveCount(before + 1);
  await settled(page);
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
  await sendAndSettle(page, firstMessage);
  await expect(page).toHaveURL(/\/c\/[^/]+$/);
  await expect(replyText(page)).not.toHaveText(/^\s*$/);
  return conversationIdFrom(page);
}

function priceIn(text: string): number {
  const matches = [...text.matchAll(/\$([\d,]+(?:\.\d+)?)/g)];
  const last = matches.at(-1)?.[1];
  return last ? Number.parseFloat(last.replaceAll(',', '')) : 0;
}

async function rowLabel(row: Locator): Promise<string> {
  return (await row.locator('label').first().innerText()).trim();
}

async function optionLabels(group: Locator): Promise<string[]> {
  const rows = optionRows(group);
  await expect(rows.first()).toBeVisible();
  const total = await rows.count();
  const labels: string[] = [];
  for (let index = 0; index < total; index += 1) {
    labels.push(await rowLabel(rows.nth(index)));
  }
  return labels;
}

async function checkedLabel(group: Locator): Promise<string> {
  const rows = optionRows(group);
  await expect(rows.first()).toBeVisible();
  const total = await rows.count();
  for (let index = 0; index < total; index += 1) {
    const row = rows.nth(index);
    if (await row.getByRole('radio').isChecked()) {
      return rowLabel(row);
    }
  }
  return '';
}

async function pickOther(group: Locator): Promise<string> {
  const rows = optionRows(group);
  await expect(rows.first()).toBeVisible();
  const total = await rows.count();
  for (let index = 0; index < total; index += 1) {
    const row = rows.nth(index);
    if (!(await row.getByRole('radio').isChecked())) {
      await row.locator('label').first().click();
      await expect(row.getByRole('radio')).toBeChecked();
      return rowLabel(row);
    }
  }
  throw new Error('The list has only one option, so there is nothing else to pick');
}

async function checkedRow(group: Locator): Promise<Locator> {
  const rows = optionRows(group);
  const total = await rows.count();
  for (let index = 0; index < total; index += 1) {
    const row = rows.nth(index);
    if (await row.getByRole('radio').isChecked()) {
      return row;
    }
  }
  throw new Error('No option is checked');
}

async function snapshot(card: Locator): Promise<CardSnapshot> {
  return {
    flights: await optionLabels(optionGroup(card, 'Flight options')),
    hotels: await optionLabels(optionGroup(card, 'Hotel options')),
    activities: await itineraryDays(card).locator('li').allInnerTexts(),
  };
}

function amount(text: string, pattern: RegExp): number {
  const found = pattern.exec(text)?.[1];
  if (!found) {
    throw new Error(`Budget text has no match for ${pattern}: ${text}`);
  }
  return Number.parseFloat(found.replaceAll(',', ''));
}

async function readBudget(card: Locator): Promise<BudgetReading> {
  const text = await budgetRegion(card).innerText();
  const limit = /of\s+\$([\d,]+\.\d{2})\s+budget/;
  return {
    total: amount(text, /total\s+\$([\d,]+\.\d{2})/i),
    limit: limit.test(text) ? amount(text, limit) : null,
    flight: amount(text, /Flight\s+\$([\d,]+\.\d{2})/),
    hotel: amount(text, /Hotel\s+\$([\d,]+\.\d{2})/),
    daily: amount(text, /Daily\s+\$([\d,]+\.\d{2})/),
  };
}

async function expectOpenCard(card: Locator): Promise<void> {
  await expect(card).toBeVisible({ timeout: REPLY_TIMEOUT_MS });
  await expect(statusChip(card, 'Approved')).toHaveCount(0);
  await expect(statusChip(card, 'Replaced')).toHaveCount(0);
  await expect(card.getByRole('radio').first()).toBeEnabled();
}

async function expectApprovalPills(page: Page): Promise<void> {
  await expect(pillButton(page, 'Approve')).toBeVisible();
  await expect(pillButton(page, 'Reject')).toBeVisible();
}

async function watchChipVersusText(page: Page): Promise<void> {
  await conversationLog(page).evaluate((log) => {
    const holder = window as unknown as { __order: string[] };
    holder.__order = [];
    const initial = log.children.length;
    new MutationObserver(() => {
      if (log.children.length <= initial) {
        return;
      }
      const last = log.lastElementChild;
      const chip = last?.querySelector('[role="group"][aria-label="Tool progress"]') ?? null;
      const text = last?.querySelectorAll('p')[1]?.textContent ?? '';
      if (chip && !holder.__order.includes('chip')) {
        holder.__order.push('chip');
      }
      if (text.trim() !== '' && !holder.__order.includes('text')) {
        holder.__order.push('text');
      }
    }).observe(log, { subtree: true, childList: true, characterData: true });
  });
}

function providerCalls(client: string): number {
  const logs = compose(['logs', '--no-color', 'q-query']);
  const pattern = new RegExp(
    `\\[\\d\\d:\\d\\d:\\d\\d [^\\]\\n]*INF[^\\]\\n]*\\][^\\n]{0,12}Provider call ${client}`,
    'g',
  );
  return logs.match(pattern)?.length ?? 0;
}

function lookupViaMcp(url: string): string {
  return compose(['exec', '-T', 'q-mcp', 'curl', '-fsS', url]);
}

test.describe('sprint-1 Tokyo trip', () => {
  test.describe.configure({ mode: 'serial', timeout: TRIP_TEST_TIMEOUT_MS });

  let context: BrowserContext | undefined;
  let page: Page;
  let secondTab: Page | undefined;
  let tokyoId = '';

  test.beforeAll(async ({ browser }) => {
    test.setTimeout(SIGN_IN_TIMEOUT_MS + 60_000);
    requirePlanEnv();
    const creds = requireCreds('E2E_USER_A');
    context = await signedInPage(browser, creds);
    const first = context.pages()[0];
    if (!first) {
      throw new Error('The signed-in page is missing');
    }
    page = first;
  });

  test.afterAll(async () => {
    await context?.close();
  });

  test(
    'E2E-1 [Happy Case] demo request asks for origin, then shows one plan card',
    { annotation: { type: 'covers', description: 'SC-1' } },
    async () => {
      tokyoId = await createConversation(page, DEMO_REQUEST);
      await expect(planCards(page)).toHaveCount(0);
      await expect(toolChips(assistantBubbles(page).last())).toHaveCount(0);

      await watchChipVersusText(page);
      await sendAndSettle(page, `From Singapore (SIN), starting ${isoIn(5)}.`);

      await expect(planCards(page)).toHaveCount(1);
      const card = latestCard(page);
      await expectOpenCard(card);
      await expect(optionGroup(card, 'Flight options').getByRole('radio').first()).toBeVisible();
      await expect(optionGroup(card, 'Hotel options').getByRole('radio').first()).toBeVisible();
      await expectApprovalPills(page);
      await expect(page).toHaveURL(new RegExp(`/c/${escapeRegExp(tokyoId)}$`));
    },
  );

  test(
    'E2E-5 [Happy Case] running tool is shown before the answer',
    { annotation: { type: 'covers', description: 'SC-5' } },
    async () => {
      const turn = assistantBubbles(page).last();
      await expect(
        toolChips(turn).getByText('flights__search', { exact: true }).first(),
      ).toBeVisible();
      const order = await page.evaluate(() => (window as unknown as { __order: string[] }).__order);
      expect(order[0], 'a tool chip must appear before any answer text').toBe('chip');
    },
  );

  test(
    'E2E-3 [Happy Case] plan card shows days, weather and a budget that follows the hotel',
    { annotation: { type: 'covers', description: 'SC-3' } },
    async () => {
      const card = latestCard(page);
      const days = itineraryDays(card);
      await expect(days).toHaveCount(5);
      for (let index = 0; index < 5; index += 1) {
        await expect(days.nth(index).locator('li').first()).toBeVisible();
        await expect(days.nth(index)).toContainText(MONEY_PATTERN);
      }
      await expect(days.filter({ hasText: NO_FORECAST })).toHaveCount(0);

      const budget = budgetRegion(card);
      await expect(budget).toContainText(/Estimated total \$[\d,]+\.\d{2}/);
      await expect(budget).not.toContainText('not counted');
      await expect(budget.getByRole('alert')).toHaveCount(0);

      const hotels = optionGroup(card, 'Hotel options');
      const before = await readBudget(card);
      expect(before.limit).toBeNull();
      expect(before.total).toBeCloseTo(before.flight + before.hotel + before.daily, 2);

      const picked = await pickOther(hotels);
      expect(picked).not.toBe('');
      const row = await checkedRow(hotels);
      const price = priceIn(await row.innerText());
      await expect.poll(async () => (await readBudget(card)).hotel).toBeCloseTo(price, 2);
      const after = await readBudget(card);
      expect(after.total).toBeCloseTo(after.flight + price + after.daily, 2);
      expect(after.daily).toBeCloseTo(before.daily, 2);
    },
  );

  test(
    'E2E-8 [Happy Case] reject, name a change, get a new card for only that part',
    { annotation: { type: 'covers', description: 'SC-8' } },
    async () => {
      if (!context) {
        throw new Error('The browser context is missing');
      }
      const first = latestCard(page);
      const original = await snapshot(first);

      const tab = await context.newPage();
      secondTab = tab;
      await tab.goto(`/c/${tokyoId}`);
      await expect(latestCard(tab)).toBeVisible({ timeout: REPLY_TIMEOUT_MS });
      await expect(pillButton(tab, 'Approve')).toBeVisible();

      await clickPillAndSettle(page, 'Reject');
      await expect(replyText(page)).not.toHaveText(/^\s*$/);
      await expect(planCards(page)).toHaveCount(1);

      await sendAndSettle(page, 'A cheaper hotel, please.');
      await expect(planCards(page)).toHaveCount(2);
      await expect(statusChip(planCards(page).first(), 'Replaced')).toBeVisible();
      const replacement = latestCard(page);
      await expectOpenCard(replacement);

      const changed = await snapshot(replacement);
      expect(changed.flights).toEqual(original.flights);
      expect(changed.activities).toEqual(original.activities);
      expect(changed.hotels).not.toEqual(original.hotels);
      expect(changed.hotels.length).toBeGreaterThan(0);
      await expectApprovalPills(page);
    },
  );

  test(
    'E2E-14 a budget change asks where the money goes, then re-plans only that part',
    {
      annotation: {
        type: 'covers',
        description: 'Constraint: a budget change asks where the money goes, then redoes that part',
      },
    },
    async () => {
      const original = await snapshot(latestCard(page));
      const cardsBefore = await planCards(page).count();

      await sendAndSettle(page, 'Raise the budget to $5000.');
      await expect(replyText(page)).not.toHaveText(/^\s*$/);
      await expect(planCards(page)).toHaveCount(cardsBefore);

      await sendAndSettle(page, 'Put the extra money on the hotel.');
      await expect(planCards(page)).toHaveCount(cardsBefore + 1);
      await expect(statusChip(planCards(page).nth(cardsBefore - 1), 'Replaced')).toBeVisible();

      const replacement = latestCard(page);
      await expectOpenCard(replacement);
      const changed = await snapshot(replacement);
      expect(changed.flights).toEqual(original.flights);
      expect(changed.activities).toEqual(original.activities);
      expect(changed.hotels.length).toBeGreaterThan(0);
      expect((await readBudget(replacement)).limit).toBeCloseTo(5000, 2);
    },
  );

  test(
    'E2E-13 an outdated Approve does not approve',
    {
      annotation: {
        type: 'covers',
        description: 'Constraint: an Approve for an older plan card does not approve',
      },
    },
    async () => {
      const tab = secondTab;
      if (!tab) {
        throw new Error('The second tab was not opened by the earlier test');
      }
      const cardsBefore = await planCards(page).count();

      await expect(pillButton(tab, 'Approve')).toBeVisible();
      await clickPillAndSettle(tab, 'Approve');
      await expect(assistantBubbles(tab).last()).toContainText(/nothing was approved/i);

      await expect(planCards(tab)).toHaveCount(2);
      await expect(statusChip(planCards(tab).first(), 'Replaced')).toBeVisible();
      await expectOpenCard(latestCard(tab));
      await expect(statusChip(conversationLog(tab), 'Approved')).toHaveCount(0);
      await tab.close();
      secondTab = undefined;

      await page.reload();
      await expect(planCards(page)).toHaveCount(cardsBefore + 1, { timeout: REPLY_TIMEOUT_MS });
      await expect(statusChip(conversationLog(page), 'Approved')).toHaveCount(0);
      await expectOpenCard(latestCard(page));
    },
  );

  test(
    'E2E-7 [Happy Case] latest plan card survives refresh and reopen',
    { annotation: { type: 'covers', description: 'SC-7' } },
    async () => {
      const card = latestCard(page);
      await expectOpenCard(card);
      const flightCount = await optionRows(optionGroup(card, 'Flight options')).count();
      const hotelCount = await optionRows(optionGroup(card, 'Hotel options')).count();
      expect(flightCount).toBeGreaterThan(0);
      expect(hotelCount).toBeGreaterThan(0);

      const expectShown = async (): Promise<void> => {
        const shown = latestCard(page);
        await expectOpenCard(shown);
        await expect(optionRows(optionGroup(shown, 'Flight options'))).toHaveCount(flightCount);
        await expect(optionRows(optionGroup(shown, 'Hotel options'))).toHaveCount(hotelCount);
        await expectApprovalPills(page);
      };

      await page.reload();
      await expectShown();

      await startNewConversation(page);
      await historyLink(page, tokyoId).click();
      await expectShown();
    },
  );

  test(
    'E2E-2 [Happy Case] pick a flight and a hotel, then Approve confirms the trip',
    { annotation: { type: 'covers', description: 'SC-2' } },
    async () => {
      const card = latestCard(page);
      await expectOpenCard(card);
      const flight = await pickOther(optionGroup(card, 'Flight options'));
      const hotel = await pickOther(optionGroup(card, 'Hotel options'));

      await clickPillAndSettle(page, 'Approve');

      const confirmation = assistantBubbles(page).last();
      await expect(confirmation).toContainText(/confirmed/i);
      await expect(confirmation).toContainText(flight);
      await expect(confirmation).toContainText(hotel);

      const approved = latestCard(page);
      await expect(statusChip(approved, 'Approved')).toBeVisible();
      expect(await checkedLabel(optionGroup(approved, 'Flight options'))).toBe(flight);
      expect(await checkedLabel(optionGroup(approved, 'Hotel options'))).toBe(hotel);
    },
  );

  test(
    'E2E-4 [Happy Case] every option and the confirmed picks link to booking pages',
    { annotation: { type: 'covers', description: 'SC-4' } },
    async () => {
      test.setTimeout(SINGLE_TEST_TIMEOUT_MS);
      const approved = latestCard(page);
      await expect(statusChip(approved, 'Approved')).toBeVisible();

      const confirmation = assistantBubbles(page).last();
      for (const name of ['Flight options', 'Hotel options'] as const) {
        const row = await checkedRow(optionGroup(approved, name));
        const href = await row.getByRole('link').getAttribute('href');
        expect(href).toMatch(/^https:\/\//);
        await expect(confirmation).toContainText(href ?? '');
      }

      const links = approved.getByRole('link', { name: /^Book / });
      const total = await links.count();
      expect(total).toBeGreaterThanOrEqual(2);
      for (let index = 0; index < total; index += 1) {
        const link = links.nth(index);
        await expect(link).toHaveAttribute('href', /^https:\/\//);
        await expect(link).toHaveAttribute('target', '_blank');
        await expect(link).toHaveAttribute('rel', /noopener/);

        const [popup] = await Promise.all([page.context().waitForEvent('page'), link.click()]);
        await expect.poll(() => popup.url(), { timeout: LINK_TIMEOUT_MS }).toMatch(/^https:\/\//);
        expect(new URL(popup.url()).host).not.toBe(APP_HOST);
        await popup.close();
      }
    },
  );

  test(
    'E2E-12 a change after Approve needs Approve again',
    {
      annotation: {
        type: 'covers',
        description: 'Constraint: a change after Approve shows a new plan card',
      },
    },
    async () => {
      const cardsBefore = await planCards(page).count();
      await expect(statusChip(latestCard(page), 'Approved')).toBeVisible();

      await sendAndSettle(page, 'Please make it 6 days instead.');

      await expect(planCards(page)).toHaveCount(cardsBefore + 1);
      const previous = planCards(page).nth(cardsBefore - 1);
      await expect(statusChip(previous, 'Replaced')).toBeVisible();
      await expect(statusChip(previous, 'Approved')).toHaveCount(0);

      const replacement = latestCard(page);
      await expectOpenCard(replacement);
      await expect(itineraryDays(replacement)).toHaveCount(6);
      await expectApprovalPills(page);
    },
  );
});

test.describe('sprint-1 conversation behaviour', () => {
  test.describe.configure({ timeout: SINGLE_TEST_TIMEOUT_MS });

  test(
    'E2E-6 [Happy Case] clicking a suggestion pill sends it',
    { annotation: { type: 'covers', description: 'SC-6' } },
    async ({ browser }) => {
      requirePlannerEnv();
      const creds = requireCreds('E2E_USER_A');
      await asUser(browser, creds, async (page) => {
        await createConversation(page, "What's the weather in Tokyo?");

        const pills = suggestionPills(page);
        await expect(pills).toBeVisible({ timeout: REPLY_TIMEOUT_MS });
        const first = pills.getByRole('button').first();
        const text = (await first.innerText()).trim();
        expect(text).not.toBe('');

        await first.click();
        await expect(assistantBubbles(page)).toHaveCount(2);
        await expect(userBubbles(page).last()).toContainText(text);
        await settled(page);
        await expect(replyText(page)).not.toHaveText(/^\s*$/);
      });
    },
  );

  test(
    'E2E-9 [Happy Case] off-topic refused, plain weather answered directly',
    { annotation: { type: 'covers', description: 'SC-9' } },
    async ({ browser }) => {
      requirePlannerEnv();
      const creds = requireCreds('E2E_USER_A');
      await asUser(browser, creds, async (page) => {
        await createConversation(page, 'Can you give me a recipe for chocolate cake?');
        await expect(planCards(page)).toHaveCount(0);
        await expect(toolChips(assistantBubbles(page).last())).toHaveCount(0);

        await sendAndSettle(page, "what's the weather in Tokyo?");
        await expect(replyText(page)).not.toHaveText(/^\s*$/);
        await expect(
          toolChips(assistantBubbles(page).last())
            .getByText('weather__forecast', { exact: true })
            .first(),
        ).toBeVisible();
        await expect(planCards(page)).toHaveCount(0);
      });
    },
  );

  test(
    'E2E-10 [Happy Case] new conversation gets a short model-written title',
    { annotation: { type: 'covers', description: 'SC-10' } },
    async ({ browser }) => {
      requirePlannerEnv();
      const creds = requireCreds('E2E_USER_A');
      await asUser(browser, creds, async (page) => {
        const message =
          'I would like a relaxed week in Lisbon with my parents next spring, around mid budget';
        const id = await createConversation(page, message);

        await expect(historyItems(page).first()).toHaveAttribute('href', `/c/${id}`);
        const cutOff = message.slice(0, 20);
        await expect
          .poll(async () => (await historyItems(page).first().innerText()).trim(), {
            timeout: 60_000,
          })
          .not.toContain(cutOff);
        const title = (await historyItems(page).first().innerText()).trim();
        expect(title.length).toBeGreaterThan(0);
        expect(title.length).toBeLessThanOrEqual(60);
        expect(title).not.toBe(message);
      });
    },
  );
});

test.describe('sprint-1 data layer', () => {
  test.describe.configure({ timeout: SINGLE_TEST_TIMEOUT_MS });

  test(
    'E2E-11 [Happy Case] repeated lookup is served from cache',
    { annotation: { type: 'covers', description: 'SC-11' } },
    async () => {
      const start = 2 + Math.floor(Math.random() * 8);
      const end = start + 1 + Math.floor(Math.random() * 4);
      const url = `http://q-query:8080/weather/forecast?city=Tokyo&startDate=${isoIn(start)}&endDate=${isoIn(end)}`;
      const client = 'weather__open_meteo';

      const before = providerCalls(client);
      const first = lookupViaMcp(url);
      expect(first.trim()).not.toBe('');
      const afterFirst = providerCalls(client);
      expect(afterFirst - before).toBeLessThanOrEqual(1);

      const keys = compose([
        'exec',
        '-T',
        'redis',
        'redis-cli',
        '--scan',
        '--pattern',
        '*weather__open_meteo*',
      ]);
      expect(keys.trim(), 'the lookup must be stored in Redis').not.toBe('');

      compose(['restart', 'q-query']);
      compose(['up', '-d', '--wait', '--wait-timeout', '120', 'q-query']);

      const second = lookupViaMcp(url);
      expect(second.trim()).toBe(first.trim());
      expect(providerCalls(client), 'the repeat must not reach the provider').toBe(afterFirst);
    },
  );

  test(
    'E2E-15 over budget warns but swaps nothing',
    {
      annotation: {
        type: 'covers',
        description: 'Constraint: over budget, the planner warns and nothing is swapped',
      },
    },
    async ({ browser }) => {
      requirePlanEnv();
      const creds = requireCreds('E2E_USER_A');
      await asUser(browser, creds, async (page) => {
        await createConversation(
          page,
          `Plan 5 days in Tokyo for 1 traveller with a total budget of $600, food, flying from Singapore (SIN), starting ${isoIn(10)}.`,
        );

        await expect(planCards(page)).toHaveCount(1);
        const card = latestCard(page);
        await expectOpenCard(card);
        const budget = budgetRegion(card);
        await expect(budget.getByRole('alert')).toContainText(/over budget/i);

        const reading = await readBudget(card);
        expect(reading.limit).toBeCloseTo(600, 2);
        expect(reading.total).toBeGreaterThan(reading.limit ?? Number.POSITIVE_INFINITY);
        expect(reading.total).toBeCloseTo(reading.flight + reading.hotel + reading.daily, 2);

        const flights = optionGroup(card, 'Flight options');
        const hotels = optionGroup(card, 'Hotel options');
        expect(await checkedLabel(flights)).toBe((await optionLabels(flights))[0]);
        expect(await checkedLabel(hotels)).toBe((await optionLabels(hotels))[0]);
        await expect(statusChip(conversationLog(page), 'Replaced')).toHaveCount(0);
        await expect(statusChip(conversationLog(page), 'Approved')).toHaveCount(0);
        await expectApprovalPills(page);
      });
    },
  );

  test(
    'E2E-16 trip beyond forecast range still gets planned',
    {
      annotation: {
        type: 'covers',
        description:
          'Constraint: a trip beyond the forecast range gets "no forecast available yet"',
      },
    },
    async ({ browser }) => {
      requirePlanEnv();
      const creds = requireCreds('E2E_USER_A');
      await asUser(browser, creds, async (page) => {
        await createConversation(
          page,
          `Plan 4 days in Tokyo for 1 traveller, mid-budget, food, flying from Singapore (SIN), starting ${isoIn(60)}.`,
        );

        const card = latestCard(page);
        await expectOpenCard(card);
        const days = itineraryDays(card);
        const total = await days.count();
        expect(total).toBeGreaterThan(0);
        await expect(days.filter({ hasText: NO_FORECAST })).toHaveCount(total);
        await expectApprovalPills(page);
      });
    },
  );

  test(
    'E2E-17 missing data key disables only that source',
    {
      annotation: {
        type: 'covers',
        description: 'Constraint: a missing key disables only that data source',
      },
    },
    async ({ browser }) => {
      requirePlanEnv();
      const creds = requireCreds('E2E_USER_A');

      const dir = mkdtempSync(join(tmpdir(), 'e2e-override-'));
      const override = join(dir, 'no-hotel-key.yaml');
      writeFileSync(
        override,
        'services:\n  q-query:\n    environment:\n      ProviderKeys__hotels__serpapi: ""\n',
      );
      try {
        compose([
          '-f',
          join(REPO_ROOT, 'compose.yaml'),
          '-f',
          override,
          'up',
          '-d',
          '--wait',
          '--wait-timeout',
          '300',
          'q-query',
        ]);

        await asUser(browser, creds, async (page) => {
          await createConversation(page, 'Plan 4 days in Tokyo for 1 traveller, mid-budget, food.');
          await sendAndSettle(page, `From Singapore (SIN), starting ${isoIn(5)}.`);

          const turn = assistantBubbles(page).last();
          await expect(
            turn.getByRole('alert').filter({ hasText: /hotels? data is unavailable/i }),
          ).toBeVisible();
          await expect(
            toolChips(turn).getByText('hotels__search', { exact: true }).first(),
          ).toBeVisible();

          const card = latestCard(page);
          await expect(card).toBeVisible();
          await expect(
            optionGroup(card, 'Flight options').getByRole('radio').first(),
          ).toBeVisible();
          await expect(optionGroup(card, 'Hotel options')).toHaveCount(0);
          const days = itineraryDays(card);
          await expect(days.first()).toBeVisible();
          await expect(days.filter({ hasNotText: NO_FORECAST })).not.toHaveCount(0);
          await expect(pillButton(page, 'Approve')).toHaveCount(0);
        });
      } finally {
        rmSync(dir, { recursive: true, force: true });
        try {
          compose(['up', '-d', '--wait', '--wait-timeout', '300', 'q-query']);
        } catch (error) {
          test.info().annotations.push({
            type: 'cleanup-failed',
            description: `q-query was not restored to the default compose file: ${String(error)}`,
          });
        }
      }
    },
  );
});
