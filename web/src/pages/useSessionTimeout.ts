import { useCallback, useEffect, useRef, useState } from 'react';
import { getMe, getSessionTimeLeft, UnauthorizedError } from '../api/api';
import type { SessionTimeLeft } from '../api/schemas';

const WARN_SECONDS = 120;
const RETRY_MS = 60_000;
const MS_PER_SECOND = 1000;

export type SessionWarningState = { secondsLeft: number };

export function useSessionTimeout() {
  const [warning, setWarning] = useState<SessionWarningState | null>(null);
  const [ended, setEnded] = useState(false);
  const [staying, setStaying] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const alive = useRef(false);
  const finished = useRef(false);
  const readRef = useRef<() => Promise<void>>(() => Promise.resolve());

  const clearTimer = useCallback(() => {
    if (timer.current !== null) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  const scheduleRead = useCallback(
    (delayMs: number) => {
      clearTimer();
      timer.current = setTimeout(() => void readRef.current(), delayMs);
    },
    [clearTimer],
  );

  const finish = useCallback(() => {
    finished.current = true;
    clearTimer();
    setWarning(null);
    setEnded(true);
  }, [clearTimer]);

  const applyReading = useCallback(
    (reading: SessionTimeLeft) => {
      if (reading.secondsLeft === 0) {
        finish();
      } else if (reading.endsBy === 'idle' && reading.secondsLeft <= WARN_SECONDS) {
        setWarning({ secondsLeft: reading.secondsLeft });
        scheduleRead((reading.secondsLeft + 1) * MS_PER_SECOND);
      } else if (reading.endsBy === 'idle') {
        setWarning(null);
        scheduleRead((reading.secondsLeft - WARN_SECONDS) * MS_PER_SECOND);
      } else {
        setWarning(null);
        scheduleRead((reading.secondsLeft + 1) * MS_PER_SECOND);
      }
    },
    [finish, scheduleRead],
  );

  const read = useCallback(async () => {
    clearTimer();
    try {
      const reading = await getSessionTimeLeft();
      if (alive.current && !finished.current) {
        applyReading(reading);
      }
    } catch (caught) {
      if (!alive.current || finished.current) {
        return;
      }
      if (caught instanceof UnauthorizedError) {
        finish();
      } else {
        scheduleRead(RETRY_MS);
      }
    }
  }, [applyReading, clearTimer, finish, scheduleRead]);

  useEffect(() => {
    readRef.current = read;
    alive.current = true;
    finished.current = false;
    void read();
    return () => {
      alive.current = false;
      clearTimer();
    };
  }, [read, clearTimer]);

  const stay = useCallback(async () => {
    setStaying(true);
    try {
      await getMe();
    } catch (caught) {
      if (caught instanceof UnauthorizedError && alive.current) {
        finish();
      }
    }
    if (alive.current && !finished.current) {
      await readRef.current();
    }
    if (alive.current) {
      setStaying(false);
    }
  }, [finish]);

  const end = useCallback(() => {
    finish();
  }, [finish]);

  return { warning, ended, staying, stay, end };
}
