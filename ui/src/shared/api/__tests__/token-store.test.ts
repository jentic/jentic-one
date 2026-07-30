import { afterEach, describe, expect, it, vi } from 'vitest';
import {
	clearToken,
	consumeSessionExpiredNotice,
	getSessionExpiresAt,
	getToken,
	setSession,
	setToken,
	subscribeToken,
} from '@/shared/api/token-store';

const STORAGE_KEY = 'jentic-one.access_token';
const EXPIRES_AT_KEY = 'jentic-one.access_token_expires_at';

describe('token store', () => {
	afterEach(() => {
		clearToken();
		sessionStorage.clear();
	});

	it('starts empty and round-trips a token through localStorage', () => {
		expect(getToken()).toBeNull();
		setToken('abc');
		expect(getToken()).toBe('abc');
		expect(localStorage.getItem(STORAGE_KEY)).toBe('abc');
	});

	it('clears the token from memory and storage', () => {
		setToken('abc');
		clearToken();
		expect(getToken()).toBeNull();
		expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
	});

	it('notifies subscribers on change and stops after unsubscribe', () => {
		const listener = vi.fn();
		const unsubscribe = subscribeToken(listener);

		setToken('one');
		expect(listener).toHaveBeenLastCalledWith('one');

		// No-op when the value is unchanged.
		setToken('one');
		expect(listener).toHaveBeenCalledTimes(1);

		unsubscribe();
		setToken('two');
		expect(listener).toHaveBeenCalledTimes(1);
	});

	it('records and persists session expiry via setSession', () => {
		const before = Date.now();
		setSession('abc', 3600);
		const expiresAt = getSessionExpiresAt();
		expect(expiresAt).not.toBeNull();
		expect(expiresAt!).toBeGreaterThanOrEqual(before + 3600_000);
		expect(expiresAt!).toBeLessThanOrEqual(Date.now() + 3600_000);
		expect(Number(localStorage.getItem(EXPIRES_AT_KEY))).toBe(expiresAt);
	});

	it('notifies subscribers when setSession adopts a new token', () => {
		const listener = vi.fn();
		const unsubscribe = subscribeToken(listener);
		setSession('fresh', 60);
		expect(listener).toHaveBeenLastCalledWith('fresh');
		unsubscribe();
	});

	it('drops the expiry alongside the token on clear', () => {
		setSession('abc', 3600);
		clearToken();
		expect(getSessionExpiresAt()).toBeNull();
		expect(localStorage.getItem(EXPIRES_AT_KEY)).toBeNull();
	});

	it('reports no expiry for tokens adopted via legacy setToken', () => {
		setToken('legacy');
		expect(getSessionExpiresAt()).toBeNull();
	});

	it('drops a previous session expiry when a legacy setToken replaces it', () => {
		setSession('with-expiry', 3600);
		setToken('legacy-replacement');
		expect(getSessionExpiresAt()).toBeNull();
		expect(localStorage.getItem(EXPIRES_AT_KEY)).toBeNull();
	});

	it('records a session-expired notice that reads exactly once', () => {
		setSession('abc', 3600);
		clearToken('session-expired');
		expect(consumeSessionExpiredNotice()).toBe(true);
		// One-shot: consuming clears the flag.
		expect(consumeSessionExpiredNotice()).toBe(false);
	});

	it('does not flag plain sign-outs as expired sessions', () => {
		setSession('abc', 3600);
		clearToken();
		expect(consumeSessionExpiredNotice()).toBe(false);
	});

	it('clears a pending expired notice when a new session is adopted', () => {
		clearToken('session-expired');
		setSession('fresh', 3600);
		expect(consumeSessionExpiredNotice()).toBe(false);
	});
});
