import { expect, type Page } from '@playwright/test';
import { REPLY_TIMEOUT_MS } from './env';
import {
  assistantBubbles,
  messageBox,
  newConversationLink,
  type PillName,
  pillButton,
  replyText,
} from './locators';

export async function settled(page: Page): Promise<void> {
  await expect(messageBox(page)).toBeEnabled({ timeout: REPLY_TIMEOUT_MS });
}

export async function sendAndSettle(page: Page, text: string): Promise<void> {
  const before = await assistantBubbles(page).count();
  await messageBox(page).fill(text);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(assistantBubbles(page)).toHaveCount(before + 1);
  await settled(page);
}

export async function clickPillAndSettle(page: Page, name: PillName): Promise<void> {
  const before = await assistantBubbles(page).count();
  const pill = pillButton(page, name);
  await expect(pill).toBeEnabled({ timeout: REPLY_TIMEOUT_MS });
  await pill.click();
  await expect(assistantBubbles(page)).toHaveCount(before + 1);
  await settled(page);
}

export function conversationIdFrom(page: Page): string {
  const match = /\/c\/([^/?#]+)/.exec(page.url());
  if (!match?.[1]) {
    throw new Error(`Not on a conversation route: ${page.url()}`);
  }
  return match[1];
}

export async function startNewConversation(page: Page): Promise<void> {
  await newConversationLink(page).click();
  await expect(assistantBubbles(page)).toHaveCount(0);
}

export async function createConversation(page: Page, firstMessage: string): Promise<string> {
  await startNewConversation(page);
  await sendAndSettle(page, firstMessage);
  await expect(page).toHaveURL(/\/c\/[^/]+$/);
  await expect(replyText(page)).not.toHaveText(/^\s*$/);
  return conversationIdFrom(page);
}
