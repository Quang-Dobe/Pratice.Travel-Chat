import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { parseEnv } from 'node:util';
import { defineConfig } from '@playwright/test';

const rootEnv = resolve(import.meta.dirname, '../.env');
if (existsSync(rootEnv)) {
  process.loadEnvFile(rootEnv);
}

const testEnv = resolve(import.meta.dirname, '../.env.test');
if (existsSync(testEnv)) {
  for (const [name, value] of Object.entries(parseEnv(readFileSync(testEnv, 'utf8')))) {
    process.env[name] = value;
  }
}

const localEnv = resolve(import.meta.dirname, './.env.e2e');
if (existsSync(localEnv)) {
  process.loadEnvFile(localEnv);
}

export default defineConfig({
  testDir: './e2e',
  workers: 1,
  fullyParallel: false,
  timeout: 120_000,
  expect: { timeout: 30_000 },
  globalSetup: './e2e/global-setup.ts',
  globalTeardown: './e2e/global-teardown.ts',
  use: {
    baseURL: 'http://localhost:8080',
    actionTimeout: 30_000,
    navigationTimeout: 60_000,
  },
});
