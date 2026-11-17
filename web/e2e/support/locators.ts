import type { Locator, Page } from '@playwright/test';

type OptionGroupName = 'Flight options' | 'Hotel options';
export type PillName = 'Approve' | 'Reject';

export const newConversationLink = (page: Page): Locator =>
  page.getByRole('link', { name: 'New conversation' });
export const historyItems = (page: Page): Locator =>
  page.getByRole('list', { name: 'Conversation history' }).locator('a');
export const historyLink = (page: Page, id: string): Locator =>
  page.getByRole('list', { name: 'Conversation history' }).locator(`a[href="/c/${id}"]`);
export const conversationLog = (page: Page): Locator =>
  page.getByRole('log', { name: 'Conversation' });
export const turnBubbles = (page: Page): Locator => conversationLog(page).locator(':scope > div');
export const assistantBubbles = (page: Page): Locator =>
  turnBubbles(page).filter({ has: page.getByText('Assistant', { exact: true }) });
export const userBubbles = (page: Page): Locator =>
  turnBubbles(page).filter({ has: page.getByText('You', { exact: true }) });
export const messageBox = (page: Page): Locator => page.getByRole('textbox', { name: 'Message' });
export const replyText = (page: Page): Locator => assistantBubbles(page).last().locator('p').nth(1);
export const toolChips = (bubble: Locator): Locator =>
  bubble.getByRole('group', { name: 'Tool progress' });
export const planCards = (page: Page): Locator =>
  conversationLog(page).getByRole('group', { name: 'Plan card' });
export const latestCard = (page: Page): Locator => planCards(page).last();
export const suggestionPills = (page: Page): Locator =>
  conversationLog(page).getByRole('group', { name: 'Suggestions' });
export const pillButton = (page: Page, name: PillName): Locator =>
  suggestionPills(page).getByRole('button', { name, exact: true });
export const optionGroup = (card: Locator, name: OptionGroupName): Locator =>
  card.getByRole('radiogroup', { name });
export const optionRows = (group: Locator): Locator => group.locator(':scope > div');
export const itineraryDays = (card: Locator): Locator =>
  card.getByRole('region', { name: /^Day \d{4}-\d{2}-\d{2}$/ });
export const budgetRegion = (card: Locator): Locator =>
  card.getByRole('region', { name: 'Budget' });
export const statusChip = (card: Locator, label: 'Approved' | 'Replaced'): Locator =>
  card.getByText(label, { exact: true });
