import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const REPO_ROOT = fileURLToPath(new URL('../../', import.meta.url));

const DEFAULT_COMPOSE_TIMEOUT_MS = 900_000;
const STACK_TIMEOUT_SECONDS = '600';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function describeExecError(error: unknown): string {
  if (isRecord(error)) {
    const stderr = error.stderr;
    const text = Buffer.isBuffer(stderr) ? stderr.toString('utf8') : String(stderr ?? '');
    if (text) {
      return text.slice(-2000);
    }
  }
  return error instanceof Error ? error.message : String(error);
}

export function envFileArgs(includeTest: boolean): string[] {
  const files = [join(REPO_ROOT, '.env')];
  if (includeTest) {
    files.push(join(REPO_ROOT, '.env.test'));
  }
  return files.filter((file) => existsSync(file)).flatMap((file) => ['--env-file', file]);
}

export function runCompose(args: string[], timeoutMs = DEFAULT_COMPOSE_TIMEOUT_MS): string {
  try {
    return execFileSync('docker', ['compose', ...args], {
      cwd: REPO_ROOT,
      encoding: 'utf8',
      timeout: timeoutMs,
      maxBuffer: 64 * 1024 * 1024,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
  } catch (error) {
    throw new Error(
      `docker compose ${args.slice(0, 8).join(' ')} failed: ${describeExecError(error)}`,
    );
  }
}

export function compose(args: string[], timeoutMs = DEFAULT_COMPOSE_TIMEOUT_MS): string {
  return runCompose([...envFileArgs(true), ...args], timeoutMs);
}

export default function globalSetup(): void {
  compose(['up', '-d', '--wait', '--wait-timeout', STACK_TIMEOUT_SECONDS]);
  compose([
    'up',
    '-d',
    '--force-recreate',
    '--wait',
    '--wait-timeout',
    STACK_TIMEOUT_SECONDS,
    'q-agent',
  ]);
}
