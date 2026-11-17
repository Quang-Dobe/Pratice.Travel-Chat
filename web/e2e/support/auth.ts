import type { Browser, BrowserContext, Page } from '@playwright/test';
import {
  APP_HOST,
  BASE_URL,
  CORRELATION_FAILURE,
  type Creds,
  DENIED_HEADING,
  escapeRegExp,
  MFA_PROMPT,
  pause,
  SIGN_IN_TIMEOUT_MS,
} from './env';
import { newConversationLink } from './locators';

const FIRST_SIGN_IN_PROMPT =
  /update your password|more information required|action required|keep your account secure/i;

type Outcome = 'chat' | 'denied' | 'correlation' | 'entra' | 'pending';

export async function bodyText(page: Page): Promise<string> {
  return page
    .locator('body')
    .innerText({ timeout: 2_000 })
    .catch(() => '');
}

export async function readOutcome(page: Page): Promise<Outcome> {
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

export async function correlationError(page: Page): Promise<Error> {
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

export async function advanceEntra(page: Page, creds: Creds, done: Set<string>): Promise<boolean> {
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

export async function signIn(page: Page, creds: Creds): Promise<'chat' | 'denied'> {
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

export async function signedInPage(browser: Browser, creds: Creds): Promise<BrowserContext> {
  const context = await browser.newContext({ baseURL: BASE_URL });
  try {
    const outcome = await signIn(await context.newPage(), creds);
    if (outcome !== 'chat') {
      throw new Error(
        `${creds.email} was turned away as uninvited; its oid must be listed in users.json`,
      );
    }
    return context;
  } catch (error) {
    await context.close();
    throw error;
  }
}

export async function asUser<T>(
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
