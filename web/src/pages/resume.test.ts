import { beforeEach, describe, expect, it } from 'vitest';
import { clearResume, readResume, saveResume } from './resume';

const KEY = 'travelchat.resume';

describe('resume', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('returns the saved record for the same user', () => {
    saveResume({ userId: 'u1', conversationId: 'c1', draft: 'hello' });

    expect(readResume('u1')).toEqual({ userId: 'u1', conversationId: 'c1', draft: 'hello' });
  });

  it('keeps a null conversationId', () => {
    saveResume({ userId: 'u1', conversationId: null, draft: 'hi' });

    expect(readResume('u1')).toEqual({ userId: 'u1', conversationId: null, draft: 'hi' });
  });

  it('drops another user record', () => {
    saveResume({ userId: 'u1', conversationId: 'c1', draft: 'x' });

    expect(readResume('u2')).toBeNull();
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it('drops a malformed value', () => {
    localStorage.setItem(KEY, '{"userId":1}');

    expect(readResume('u1')).toBeNull();
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it('drops a value that is not json', () => {
    localStorage.setItem(KEY, 'not json');

    expect(readResume('u1')).toBeNull();
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it('keeps a non-empty draft when an empty-draft save follows', () => {
    saveResume({ userId: 'u1', conversationId: 'c1', draft: 'typed' });

    saveResume({ userId: 'u1', conversationId: null, draft: '' });

    expect(readResume('u1')).toEqual({ userId: 'u1', conversationId: 'c1', draft: 'typed' });
  });

  it('replaces another user record with the new user save', () => {
    saveResume({ userId: 'u1', conversationId: 'c1', draft: 'typed' });

    saveResume({ userId: 'u2', conversationId: null, draft: '' });

    expect(readResume('u2')).toEqual({ userId: 'u2', conversationId: null, draft: '' });
  });

  it('replaces an empty-draft record with another empty-draft save', () => {
    saveResume({ userId: 'u1', conversationId: 'c1', draft: '' });

    saveResume({ userId: 'u1', conversationId: 'c2', draft: '' });

    expect(readResume('u1')).toEqual({ userId: 'u1', conversationId: 'c2', draft: '' });
  });

  it('clearResume removes the key', () => {
    saveResume({ userId: 'u1', conversationId: 'c1', draft: 'x' });

    clearResume();

    expect(localStorage.getItem(KEY)).toBeNull();
  });
});
