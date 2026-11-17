import { expect, type Locator } from '@playwright/test';
import { itineraryDays, optionGroup, optionRows } from './locators';

type CardSnapshot = { flights: string[]; hotels: string[]; activities: string[] };

export async function rowLabel(row: Locator): Promise<string> {
  return (await row.locator('label').first().innerText()).trim();
}

export async function optionLabels(group: Locator): Promise<string[]> {
  const rows = optionRows(group);
  await expect(rows.first()).toBeVisible();
  const total = await rows.count();
  const labels: string[] = [];
  for (let index = 0; index < total; index += 1) {
    labels.push(await rowLabel(rows.nth(index)));
  }
  return labels;
}

export async function checkedLabel(group: Locator): Promise<string> {
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

export async function pickOther(group: Locator): Promise<string> {
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

export async function checkedRow(group: Locator): Promise<Locator> {
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

export async function snapshot(card: Locator): Promise<CardSnapshot> {
  return {
    flights: await optionLabels(optionGroup(card, 'Flight options')),
    hotels: await optionLabels(optionGroup(card, 'Hotel options')),
    activities: await itineraryDays(card).locator('li').allInnerTexts(),
  };
}
