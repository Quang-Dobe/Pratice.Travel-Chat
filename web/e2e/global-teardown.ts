import { envFileArgs, runCompose } from './global-setup';

export default function globalTeardown(): void {
  runCompose([...envFileArgs(false), 'up', '-d', '--wait', '--wait-timeout', '600']);
}
