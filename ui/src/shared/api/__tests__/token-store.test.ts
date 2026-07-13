import { afterEach, describe, expect, it, vi } from 'vitest';
import { clearToken, getToken, setToken, subscribeToken } from '@/shared/api/token-store';

const STORAGE_KEY = 'jentic-one.access_token';

describe('token store', () => {
	afterEach(() => {
		clearToken();
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
});
