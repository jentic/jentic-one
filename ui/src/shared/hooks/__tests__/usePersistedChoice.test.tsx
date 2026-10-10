/**
 * usePersistedChoice — a fixed-option string preference on the shared local
 * preference storage (the workspace resizer's mechanism): defaults when nothing
 * valid is stored, writes through, survives storage failures.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { usePersistedChoice } from '@/shared/hooks/usePersistedChoice';
import { readLocalPreference, writeLocalPreference } from '@/shared/lib/localPreference';

const KEY = 'test.layout';
const CHOICES = ['list', 'cards'] as const;
const use = () => usePersistedChoice(KEY, CHOICES, 'list');

describe('usePersistedChoice', () => {
	beforeEach(() => window.localStorage.clear());
	afterEach(() => {
		window.localStorage.clear();
		vi.restoreAllMocks();
	});

	it('defaults to the fallback with nothing stored', () => {
		expect(renderHook(use).result.current[0]).toBe('list');
	});

	it('reads a stored choice on mount', () => {
		window.localStorage.setItem(KEY, 'cards');
		expect(renderHook(use).result.current[0]).toBe('cards');
	});

	it('writes the choice through, and a remount reads it back', () => {
		const { result, unmount } = renderHook(use);
		act(() => result.current[1]('cards'));
		expect(result.current[0]).toBe('cards');
		expect(window.localStorage.getItem(KEY)).toBe('cards');
		unmount();
		expect(renderHook(use).result.current[0]).toBe('cards');
	});

	it('ignores a value that is not one of the choices', () => {
		window.localStorage.setItem(KEY, 'garbage');
		expect(renderHook(use).result.current[0]).toBe('list');
	});

	it('keeps working in memory when storage throws', () => {
		vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
			throw new Error('denied');
		});
		vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
			throw new Error('quota');
		});
		const { result } = renderHook(use);
		expect(result.current[0]).toBe('list');
		act(() => result.current[1]('cards'));
		expect(result.current[0]).toBe('cards');
	});
});

describe('localPreference', () => {
	beforeEach(() => window.localStorage.clear());

	it('round-trips a value and forgets it on null', () => {
		writeLocalPreference(KEY, '42');
		expect(readLocalPreference(KEY)).toBe('42');
		writeLocalPreference(KEY, null);
		expect(readLocalPreference(KEY)).toBeNull();
	});
});
