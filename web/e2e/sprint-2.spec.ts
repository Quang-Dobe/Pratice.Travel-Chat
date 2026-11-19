import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { type BrowserContext, expect, type Locator, type Page, test } from '@playwright/test';
import { compose, REPO_ROOT } from './global-setup';
import { asUser, signedInPage } from './support/auth';
import { checkedLabel, optionLabels, pickOther, snapshot } from './support/card';
import {
  clickPillAndSettle,
  createConversation,
  sendAndSettle,
  startNewConversation,
} from './support/chat';
import {
  BASE_URL,
  DEMO_REQUEST,
  escapeRegExp,
  isoIn,
  missingModelCredentials,
  REPLY_TIMEOUT_MS,
  requireCreds,
  SIGN_IN_TIMEOUT_MS,
  SINGLE_TEST_TIMEOUT_MS,
  TRIP_TEST_TIMEOUT_MS,
} from './support/env';
import {
  assistantBubbles,
  budgetRegion,
  conversationLog,
  historyLink,
  itineraryDays,
  latestCard,
  optionGroup,
  optionRows,
  pillButton,
  planCards,
  replyText,
  statusChip,
  toolChips,
} from './support/locators';

const DRAFT_BANNER = 'Draft — not approved';
const MAP_NOTICE = 'The map is unavailable right now.';
const EXPORT_COLUMNS = ['Section', 'Date', 'Item', 'Details', 'Weather', 'Cost in USD'];
const LEG_PATTERN = /^[A-Z]{3} → [A-Z]{3} · \d{2}:\d{2} – \d{2}:\d{2}$/;
const STOPS_PATTERN = /\b([1-9]\d*) stops?\b/;
const EMOJI_PATTERN = /\p{Emoji_Presentation}|️/u;
const EXPORT_HREF = /^\/api\/conversations\/[^/]+\/exports\/[^/]+$/;
const AGENT_SERVICE = 'q-agent';

const DOCX_SCRIPT =
  'import sys,json;from docx import Document;d=Document(sys.argv[1]);' +
  "print(json.dumps({'paragraphs':[p.text for p in d.paragraphs]," +
  "'cells':[c.text for t in d.tables for r in t.rows for c in r.cells]}))";
const XLSX_SCRIPT =
  'import sys,json;from openpyxl import load_workbook;ws=load_workbook(sys.argv[1])["Plan"];' +
  "print(json.dumps([[('' if c is None else c) for c in r] for r in ws.iter_rows(values_only=True)]))";

type Download = { name: string; href: string; bytes: Buffer; headers: Record<string, string> };
type SheetRow = (string | number)[];
type DocxContent = { paragraphs: string[]; cells: string[] };

function requirePlanEnv(): void {
  const missing = missingModelCredentials();
  test.skip(missing.length > 0, `needs ${missing.join(', ')} in .env for the configured provider`);
  test.skip(!process.env.SERPAPI_API_KEY, 'needs SERPAPI_API_KEY in .env for flights and hotels');
}

const tripMap = (card: Locator): Locator => card.getByRole('region', { name: 'Trip map' });
const flightLegs = (card: Locator): Locator =>
  card.getByRole('list', { name: 'Flight legs' }).locator('li');
const mapPoints = (card: Locator): Locator =>
  card.getByRole('list', { name: 'Map points' }).locator('li');
const sourcesOf = (bubble: Locator): Locator => bubble.getByRole('region', { name: 'Sources' });

function longestHotelName(labels: string[]): string {
  const names = labels.map((label) => label.replace(/,\s*rated\b.*$/s, '').trim());
  return names.reduce((best, name) => (name.length > best.length ? name : best), names[0] ?? '');
}

async function readTotal(card: Locator): Promise<number> {
  const text = await budgetRegion(card).innerText();
  const found = /total\s+\$([\d,]+\.\d{2})/i.exec(text)?.[1];
  if (!found) {
    throw new Error(`Budget text has no total: ${text}`);
  }
  return Number.parseFloat(found.replaceAll(',', ''));
}

function formatUsd(value: number): string {
  return `$${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
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
    const holder = window as unknown as {
      __order: string[];
      __observer?: MutationObserver;
    };
    holder.__observer?.disconnect();
    holder.__order = [];
    const initial = log.children.length;
    holder.__observer = new MutationObserver(() => {
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
    });
    holder.__observer.observe(log, { subtree: true, childList: true, characterData: true });
  });
}

async function readOrder(page: Page): Promise<string[]> {
  return page.evaluate(() => (window as unknown as { __order: string[] }).__order);
}

async function expectSourcedReview(page: Page, question: string): Promise<void> {
  await watchChipVersusText(page);
  await sendAndSettle(page, question);
  const bubble = assistantBubbles(page).last();
  await expect(toolChips(bubble).getByText('search__web', { exact: true }).first()).toBeVisible();
  await expect(toolChips(bubble).getByText('fetch__page', { exact: true }).first()).toBeVisible();
  const order = await readOrder(page);
  expect(order[0], 'a tool chip must appear before any answer text').toBe('chip');
  await expect(replyText(page)).not.toHaveText(/^\s*$/);

  const links = sourcesOf(bubble).getByRole('link');
  await expect(links.first()).toBeVisible();
  const total = await links.count();
  for (let index = 0; index < total; index += 1) {
    await expect(links.nth(index)).toHaveAttribute('href', /^https:\/\//);
  }
}

async function requestFile(page: Page, ask: string, extension: string): Promise<Download> {
  await sendAndSettle(page, ask);
  const bubble = assistantBubbles(page).last();
  const link = bubble.getByRole('link', { name: new RegExp(`^Download .+\\.${extension}$`) });
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute('href', EXPORT_HREF);
  const href = await link.getAttribute('href');
  if (!href) {
    throw new Error('The download link has no href');
  }
  const name = (await link.innerText()).replace(/^Download /, '').trim();
  const response = await page.context().request.get(href);
  expect(response.status()).toBe(200);
  expect(response.headers()['content-disposition']).toMatch(/attachment/i);
  expect(response.headers()['x-content-type-options']).toBe('nosniff');
  return { name, href, bytes: await response.body(), headers: response.headers() };
}

function decodeEntities(text: string): string {
  return text
    .replaceAll('&lt;', '<')
    .replaceAll('&gt;', '>')
    .replaceAll('&quot;', '"')
    .replaceAll('&#x27;', "'")
    .replaceAll('&#39;', "'")
    .replaceAll('&amp;', '&');
}

function htmlOf(file: Download): string {
  return decodeEntities(file.bytes.toString('utf8'));
}

function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (quoted) {
      if (char === '"' && text[index + 1] === '"') {
        field += '"';
        index += 1;
      } else if (char === '"') {
        quoted = false;
      } else {
        field += char;
      }
    } else if (char === '"') {
      quoted = true;
    } else if (char === ',') {
      row.push(field);
      field = '';
    } else if (char === '\n' || char === '\r') {
      if (char === '\r' && text[index + 1] === '\n') {
        index += 1;
      }
      row.push(field);
      rows.push(row);
      row = [];
      field = '';
    } else {
      field += char;
    }
  }
  if (field !== '' || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

function csvRows(file: Download): string[][] {
  return parseCsv(file.bytes.toString('utf8').replace(/^﻿/, ''));
}

function readInAgent(file: Download, script: string): unknown {
  const dir = mkdtempSync(join(tmpdir(), 'e2e-export-'));
  const local = join(dir, file.name);
  const remote = `/tmp/${file.name}`;
  try {
    writeFileSync(local, file.bytes);
    compose(['cp', local, `${AGENT_SERVICE}:${remote}`]);
    const output = compose(['exec', '-T', AGENT_SERVICE, 'python', '-c', script, remote]);
    return JSON.parse(output);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

function docxOf(file: Download): DocxContent {
  return readInAgent(file, DOCX_SCRIPT) as DocxContent;
}

function sheetOf(file: Download): SheetRow[] {
  return readInAgent(file, XLSX_SCRIPT) as SheetRow[];
}

function expectDraftTable(rows: SheetRow[], dayCount: number, optionCount: number): void {
  expect(rows[0]).toEqual(EXPORT_COLUMNS);
  const body = rows.slice(1);
  expect(body.filter((row) => row[0] === 'Day')).toHaveLength(dayCount);
  expect(body.filter((row) => row[0] === 'Cost').length).toBeGreaterThanOrEqual(optionCount + 2);
  expect(body.every((row) => row[0] === 'Day' || row[0] === 'Cost')).toBe(true);
  expect(body.every((row) => row.length === EXPORT_COLUMNS.length)).toBe(true);
}

function restartAndWait(services: string[]): void {
  if (services.length === 0) {
    return;
  }
  compose(['restart', ...services]);
  compose(['up', '-d', '--wait', '--wait-timeout', '300', ...services]);
}

async function withComposeOverride(
  service: string,
  variable: string,
  run: () => Promise<void>,
  alsoRestart: string[] = [],
): Promise<void> {
  const dir = mkdtempSync(join(tmpdir(), 'e2e-override-'));
  const override = join(dir, 'override.yaml');
  writeFileSync(override, `services:\n  ${service}:\n    environment:\n      ${variable}: ""\n`);
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
      service,
    ]);
    restartAndWait(alsoRestart);
    await run();
  } finally {
    rmSync(dir, { recursive: true, force: true });
    try {
      compose(['up', '-d', '--wait', '--wait-timeout', '300', service]);
      restartAndWait(alsoRestart);
    } catch (error) {
      test.info().annotations.push({
        type: 'cleanup-failed',
        description: `${service} was not restored to the default compose file: ${String(error)}`,
      });
    }
  }
}

function connectingFlightIndex(labels: string[]): number {
  return labels.findIndex((label) => STOPS_PATTERN.test(label));
}

test.describe('sprint-2 Tokyo trip', () => {
  test.describe.configure({ mode: 'serial', timeout: TRIP_TEST_TIMEOUT_MS });

  let context: BrowserContext | undefined;
  let page: Page;
  let tokyoId = '';
  const draftFiles: Record<string, Download> = {};
  let approvedFlight = '';
  let approvedHotel = '';
  let confirmationText = '';
  let ownerFile: Download | undefined;

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
    'E2E-2 [Happy Case] the plan card map shows the whole trip',
    { annotation: { type: 'covers', description: 'SC-2' } },
    async () => {
      tokyoId = await createConversation(page, DEMO_REQUEST);
      await sendAndSettle(page, `From Singapore (SIN), starting ${isoIn(5)}.`);

      await expect(planCards(page)).toHaveCount(1);
      const card = latestCard(page);
      await expectOpenCard(card);
      await expect(
        tripMap(card),
        'needs GEOAPIFY_API_KEY set in .env (gateway maps proxy)',
      ).toBeVisible();
      await expect(card.getByText(MAP_NOTICE)).toHaveCount(0);
      await expect(tripMap(card)).toHaveCount(1);

      await expect(flightLegs(card).first()).toBeVisible();
      const legs = await flightLegs(card).allInnerTexts();
      for (const leg of legs) {
        expect(leg.trim()).toMatch(LEG_PATTERN);
      }

      const hotelPins = mapPoints(card).filter({ hasText: /^Hotel: / });
      const stopPins = mapPoints(card).filter({ hasText: /^Day \d+ stop \d+: / });
      await expect(hotelPins.first()).toBeAttached();
      await expect(stopPins.first()).toBeAttached();
      await expectApprovalPills(page);
    },
  );

  test(
    'E2E-3 [Happy Case] the map pans, zooms and survives refresh and reopen',
    { annotation: { type: 'covers', description: 'SC-3' } },
    async () => {
      const map = tripMap(latestCard(page));
      await map.scrollIntoViewIfNeeded();
      await expect(map).toHaveAttribute('data-zoom', /^\d+(\.\d+)?$/, { timeout: 60_000 });
      const zoomOf = async (): Promise<number> => Number(await map.getAttribute('data-zoom'));
      const before = await zoomOf();

      const box = await map.boundingBox();
      if (!box) {
        throw new Error('The trip map has no bounding box');
      }
      const startX = box.x + box.width / 2;
      const startY = box.y + box.height / 2;
      await page.mouse.move(startX, startY);
      await page.mouse.down();
      await page.mouse.move(startX + 120, startY + 40, { steps: 10 });
      await page.mouse.up();

      await map.getByRole('button', { name: 'Zoom in' }).click();
      await expect.poll(zoomOf, { timeout: 30_000 }).toBeGreaterThan(before);

      const expectMapShown = async (): Promise<void> => {
        const card = latestCard(page);
        await expectOpenCard(card);
        await expect(tripMap(card)).toBeVisible();
      };

      await page.reload();
      await expectMapShown();

      await startNewConversation(page);
      await historyLink(page, tokyoId).click();
      await expectMapShown();
    },
  );

  test(
    'E2E-11 a flight with a stop draws one line per leg',
    {
      annotation: {
        type: 'covers',
        description: 'In scope: one flight line per leg, each with its own flight times',
      },
    },
    async () => {
      const card = latestCard(page);
      const flights = optionGroup(card, 'Flight options');
      const labels = await optionLabels(flights);
      const index = connectingFlightIndex(labels);
      test.skip(index < 0, 'no connecting flight is offered on this Tokyo card');

      const row = optionRows(flights).nth(index);
      await row.locator('label').first().click();
      await expect(row.getByRole('radio')).toBeChecked();

      const stops = Number(STOPS_PATTERN.exec(labels[index] ?? '')?.[1] ?? 0);
      await expect(flightLegs(card)).toHaveCount(stops + 1);
      const legs = await flightLegs(card).allInnerTexts();
      for (const leg of legs) {
        expect(leg.trim()).toMatch(LEG_PATTERN);
      }
      await expect(tripMap(card)).toBeVisible();
    },
  );

  test(
    'E2E-4 [Happy Case] a review question searches the web and links its sources',
    { annotation: { type: 'covers', description: 'SC-4' } },
    async () => {
      test.setTimeout(SINGLE_TEST_TIMEOUT_MS);
      const card = latestCard(page);
      const hotels = await optionLabels(optionGroup(card, 'Hotel options'));
      const hotel = longestHotelName(hotels).slice(0, 80);
      const airline = (await checkedLabel(optionGroup(card, 'Flight options'))).split(',')[0];

      await expectSourcedReview(
        page,
        `Search the web for guest reviews of the hotel "${hotel}" and summarise them.`,
      );
      await expectSourcedReview(
        page,
        'Search the web for reviews of a well-loved ramen restaurant in Tokyo near my hotel.',
      );
      await expectSourcedReview(
        page,
        'Search the web for reviews of Meiji Shrine in Tokyo as a place to visit.',
      );
      await expectSourcedReview(
        page,
        `Search the web for reviews of ${airline} flights from Singapore to Tokyo.`,
      );
    },
  );

  test(
    'E2E-5 [Happy Case] restaurants and places from reviews become map pins',
    { annotation: { type: 'covers', description: 'SC-5' } },
    async () => {
      const card = latestCard(page);
      const pinned = mapPoints(card).filter({ hasText: /^(Restaurant|Place): / });
      const before = await pinned.count();

      await sendAndSettle(
        page,
        'Search the web for reviews of the best restaurants and places to visit near my hotel.',
      );

      await expect(pinned.first()).toBeAttached({ timeout: 60_000 });
      expect(await pinned.count()).toBeGreaterThanOrEqual(Math.max(before, 1));
      await expect(tripMap(latestCard(page))).toBeVisible();
    },
  );

  test(
    'E2E-8 [Happy Case] a file made before Approve is a draft',
    { annotation: { type: 'covers', description: 'SC-7' } },
    async () => {
      const card = latestCard(page);
      await expect(statusChip(card, 'Approved')).toHaveCount(0);
      const options = await snapshot(card);

      const file = await requestFile(page, 'Give me the plan as an HTML file.', 'html');
      draftFiles.html = file;
      const draftChip = assistantBubbles(page).last().getByText('Draft', { exact: true });
      await expect(draftChip).toBeVisible();
      expect(file.name).toContain('draft');

      const html = htmlOf(file);
      expect(html).toContain(DRAFT_BANNER);
      for (const summary of [...options.flights, ...options.hotels]) {
        expect(html).toContain(summary);
      }
    },
  );

  test(
    'E2E-6 [Happy Case] each file type arrives as a download link and opens',
    { annotation: { type: 'covers', description: 'SC-6' } },
    async () => {
      test.setTimeout(SINGLE_TEST_TIMEOUT_MS);
      const html = await requestFile(page, 'Give me the plan as an HTML file.', 'html');
      expect(htmlOf(html)).toContain('<h1>Trip plan');

      const docx = await requestFile(page, 'Give me the plan as a Word docx file.', 'docx');
      draftFiles.docx = docx;
      const content = docxOf(docx);
      const text = content.paragraphs.join('\n');
      expect(text).toContain('Days');
      expect(text).toContain(DRAFT_BANNER);
      expect(content.cells).toContain('Weather');

      const xlsx = await requestFile(page, 'Give me the plan as an Excel file.', 'xlsx');
      draftFiles.xlsx = xlsx;
      expect(sheetOf(xlsx)[0]).toEqual(EXPORT_COLUMNS);

      const csv = await requestFile(page, 'Give me the plan as a csv file.', 'csv');
      draftFiles.csv = csv;
      expect(csvRows(csv)[0]).toEqual(EXPORT_COLUMNS);
    },
  );

  test(
    'E2E-14 excel and csv are tables by day and by cost',
    {
      annotation: {
        type: 'covers',
        description: 'Constraint: one row per day or cost item with the six columns',
      },
    },
    async () => {
      const xlsx = draftFiles.xlsx;
      const csv = draftFiles.csv;
      if (!xlsx || !csv) {
        throw new Error('The excel and csv files were not downloaded by the earlier test');
      }
      const card = latestCard(page);
      const dayCount = await itineraryDays(card).count();
      const optionCount =
        (await optionRows(optionGroup(card, 'Flight options')).count()) +
        (await optionRows(optionGroup(card, 'Hotel options')).count());
      expect(dayCount).toBeGreaterThan(0);

      const sheet = sheetOf(xlsx);
      expectDraftTable(sheet, dayCount, optionCount);
      for (const row of sheet.slice(1)) {
        expect(typeof row[5]).toBe('number');
      }

      const table = csvRows(csv);
      expectDraftTable(table, dayCount, optionCount);
      for (const row of table.slice(1)) {
        expect(row[5]).toMatch(/^\d+\.\d{2}$/);
      }
    },
  );

  test(
    'E2E-15 export after a change uses the latest plan card',
    {
      annotation: {
        type: 'covers',
        description: 'In scope: the file is made from the latest plan card',
      },
    },
    async () => {
      const cardsBefore = await planCards(page).count();
      const original = await snapshot(latestCard(page));

      await sendAndSettle(page, 'A cheaper hotel, please.');
      await expect(planCards(page)).toHaveCount(cardsBefore + 1);
      await expect(statusChip(planCards(page).nth(cardsBefore - 1), 'Replaced')).toBeVisible();
      const replacement = latestCard(page);
      await expectOpenCard(replacement);
      const changed = await snapshot(replacement);
      expect(changed.hotels).not.toEqual(original.hotels);

      const file = await requestFile(page, 'Give me the plan as an HTML file.', 'html');
      const html = htmlOf(file);
      for (const summary of changed.hotels) {
        expect(html).toContain(summary);
      }
      for (const summary of original.hotels.filter((item) => !changed.hotels.includes(item))) {
        expect(html).not.toContain(summary);
      }
    },
  );

  test(
    'E2E-7 [Happy Case] an approved plan file matches the plan card',
    { annotation: { type: 'covers', description: 'SC-7' } },
    async () => {
      const cardsBefore = await planCards(page).count();
      await sendAndSettle(page, 'An even cheaper hotel, please.');
      await expect(planCards(page)).toHaveCount(cardsBefore + 1);
      const card = latestCard(page);
      await expectOpenCard(card);
      await expectApprovalPills(page);

      const flights = optionGroup(card, 'Flight options');
      const hotels = optionGroup(card, 'Hotel options');
      approvedFlight = await pickOther(flights);
      approvedHotel = await pickOther(hotels);
      const otherFlights = (await optionLabels(flights)).filter(
        (item) => !approvedFlight.includes(item) && !item.includes(approvedFlight),
      );
      const otherHotels = (await optionLabels(hotels)).filter(
        (item) => !approvedHotel.includes(item) && !item.includes(approvedHotel),
      );

      await clickPillAndSettle(page, 'Approve');
      confirmationText = await assistantBubbles(page).last().innerText();
      const approved = latestCard(page);
      await expect(statusChip(approved, 'Approved')).toBeVisible();
      const total = await readTotal(approved);
      const dates = await itineraryDays(approved).evaluateAll((nodes) =>
        nodes.map((node) => (node.getAttribute('aria-label') ?? '').replace('Day ', '')),
      );

      const file = await requestFile(page, 'Give me the plan as an HTML file.', 'html');
      ownerFile = file;
      const draftChip = assistantBubbles(page).last().getByText('Draft', { exact: true });
      await expect(draftChip).toHaveCount(0);
      expect(file.name).not.toContain('draft');

      const html = htmlOf(file);
      expect(html).not.toContain(DRAFT_BANNER);
      expect(html).toContain(approvedFlight);
      expect(html).toContain(approvedHotel);
      for (const other of [...otherFlights, ...otherHotels]) {
        expect(html).not.toContain(other);
      }
      for (const date of dates) {
        expect(date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
        expect(html).toContain(date);
      }
      expect(html).toContain(`Total: ${formatUsd(total)}`);
      expect(html).toContain('Weather');
    },
  );

  test(
    'E2E-13 a download link opens only for its owner',
    {
      annotation: {
        type: 'covers',
        description: 'Constraint: a download link opens only for the signed-in owner',
      },
    },
    async ({ browser }) => {
      const file = ownerFile;
      if (!file) {
        throw new Error('No export link was captured by the earlier test');
      }
      const other = requireCreds('E2E_USER_B');

      await asUser(browser, other, async (otherPage) => {
        const response = await otherPage.context().request.get(file.href, { maxRedirects: 0 });
        expect(response.status()).toBe(404);
      });

      const anonymous = await browser.newContext({ baseURL: BASE_URL });
      try {
        const response = await anonymous.request.get(file.href, { maxRedirects: 0 });
        expect(response.status()).toBe(401);
      } finally {
        await anonymous.close();
      }

      const owner = await page.context().request.get(file.href);
      expect(owner.status()).toBe(200);
      expect(owner.headers()['content-disposition']).toMatch(/attachment/i);
    },
  );

  test(
    'E2E-1 [Happy Case] the sprint-1 Tokyo demo still ends with a confirmed trip',
    { annotation: { type: 'covers', description: 'SC-1' } },
    async () => {
      expect(confirmationText).toMatch(/confirmed/i);
      expect(confirmationText).toContain(approvedFlight);
      expect(confirmationText).toContain(approvedHotel);

      await expect(statusChip(conversationLog(page), 'Approved')).toHaveCount(1);
      await expect(
        planCards(page).filter({ hasNot: page.getByText('Replaced', { exact: true }) }),
      ).toHaveCount(1);
      await expect(page).toHaveURL(new RegExp(`/c/${escapeRegExp(tokyoId)}$`));
    },
  );

  test(
    'E2E-9 [Happy Case] screen, card and replies read warm and plain',
    { annotation: { type: 'covers', description: 'SC-8' } },
    async () => {
      const testInfo = test.info();
      const card = latestCard(page);
      await card.scrollIntoViewIfNeeded();
      await testInfo.attach('plan-card', {
        body: await card.screenshot(),
        contentType: 'image/png',
      });

      const replies = assistantBubbles(page);
      const total = await replies.count();
      expect(total).toBeGreaterThanOrEqual(2);
      for (const index of [0, total - 1]) {
        await testInfo.attach(`reply-${index + 1}`, {
          body: await replies.nth(index).screenshot(),
          contentType: 'image/png',
        });
      }
      for (const text of await replies.allInnerTexts()) {
        expect(text).not.toMatch(EMOJI_PATTERN);
      }

      await startNewConversation(page);
      await expect(page.getByRole('heading', { name: /^Where to next/ })).toBeVisible();
      await expect(page.getByText(/travel buddy/i)).toBeVisible();
      await testInfo.attach('empty-chat', {
        body: await page.screenshot(),
        contentType: 'image/png',
      });
    },
  );
});

test.describe('sprint-2 edge cases', () => {
  test.describe.configure({ timeout: SINGLE_TEST_TIMEOUT_MS });

  test(
    'E2E-10 no map key shows the card without a map',
    {
      annotation: {
        type: 'covers',
        description: 'Constraint: no map key shows the card without the map and a notice',
      },
    },
    async ({ browser }) => {
      requirePlanEnv();
      const creds = requireCreds('E2E_USER_A');
      // the gateway signing key changes on restart and q-user caches its JWKS, so restart q-user too
      await withComposeOverride(
        'q-gateway',
        'Maps__ApiKey',
        async () => {
          await asUser(browser, creds, async (page) => {
            await createConversation(page, DEMO_REQUEST);
            await sendAndSettle(page, `From Singapore (SIN), starting ${isoIn(5)}.`);

            const card = latestCard(page);
            await expectOpenCard(card);
            await expect(card.getByText(MAP_NOTICE)).toBeVisible();
            await expect(tripMap(card)).toHaveCount(0);
            await expectApprovalPills(page);
          });
        },
        ['q-user'],
      );
    },
  );

  test(
    'E2E-12 a failed web search still gets an answer',
    {
      annotation: {
        type: 'covers',
        description: 'Constraint: a failed web search never blocks an answer or a plan',
      },
    },
    async ({ browser }) => {
      requirePlanEnv();
      const creds = requireCreds('E2E_USER_A');
      await withComposeOverride('q-query', 'ProviderKeys__search__serpapi', async () => {
        await asUser(browser, creds, async (page) => {
          await createConversation(page, DEMO_REQUEST);
          await sendAndSettle(page, `From Singapore (SIN), starting ${isoIn(5)}.`);

          const card = latestCard(page);
          await expectOpenCard(card);
          const hotel = (await checkedLabel(optionGroup(card, 'Hotel options'))).slice(0, 80);

          await sendAndSettle(
            page,
            `Search the web for guest reviews of the hotel "${hotel}" and summarise them.`,
          );
          await expect(replyText(page)).not.toHaveText(/^\s*$/);
          await expect(sourcesOf(assistantBubbles(page).last())).toHaveCount(0);

          await expectOpenCard(latestCard(page));
          await expect(planCards(page)).toHaveCount(1);
        });
      });
    },
  );
});
