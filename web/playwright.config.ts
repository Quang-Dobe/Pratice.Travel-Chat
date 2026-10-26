import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { defineConfig } from '@playwright/test';

for (const envFile of ['../.env', './.env.e2e']) {
  const path = resolve(import.meta.dirname, envFile);
  if (existsSync(path)) {
    process.loadEnvFile(path);
  }
}

export default defineConfig({
  testDir: './e2e',
  workers: 1,
  fullyParallel: false,
  timeout: 120_000,
  expect: { timeout: 30_000 },
  use: {
    baseURL: 'http://localhost:8080',
    actionTimeout: 30_000,
    navigationTimeout: 60_000,
  },
});
