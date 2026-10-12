import { describe, it, expect, afterEach, vi } from 'vitest';
import { readLocalPreference, writeLocalPreference } from '@/shared/lib/localPreference';

const KEY = 'test.localPreference';

describe('localPreference', () => {
	afterEach(() => {
		vi.restoreAllMocks();
		window.localStorage.removeItem(KEY);
	});

	it('reads back what it wrote, and null when nothing is stored', () => {
		expect(readLocalPreference(KEY)).toBeNull();
		writeLocalPreference(KEY, 'cards');
		expect(readLocalPreference(KEY)).toBe('cards');
	});

	it('forgets the key on a null write', () => {
		writeLocalPreference(KEY, 'cards');
		writeLocalPreference(KEY, null);
		expect(window.localStorage.getItem(KEY)).toBeNull();
	});

	it('reads a throwing storage as nothing stored', () => {
		vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
			throw new Error('SecurityError');
		});
		expect(readLocalPreference(KEY)).toBeNull();
	});

	it('drops a write the storage refuses, without throwing', () => {
		vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
			throw new Error('QuotaExceededError');
		});
		vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
			throw new Error('SecurityError');
		});
		expect(() => writeLocalPreference(KEY, 'cards')).not.toThrow();
		expect(() => writeLocalPreference(KEY, null)).not.toThrow();
	});
});
