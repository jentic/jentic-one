import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { act, renderHook, stubLocalStorage } from '@/__tests__/test-utils';
import { THEME_STORAGE_KEY, activeTheme, setTheme, useTheme } from '../theme';

/**
 * The theme module is a singleton (it paints `<html data-theme>` on import),
 * so each test starts from the default light palette with nothing stored.
 * Storage is stubbed so a switch here never reaches other test files.
 */
let meta: HTMLMetaElement;
let restoreStorage: () => void;

function useStorage(options?: { failWrites?: boolean }) {
	restoreStorage();
	restoreStorage = stubLocalStorage(options);
}

beforeEach(() => {
	restoreStorage = stubLocalStorage();
	meta = document.createElement('meta');
	meta.name = 'theme-color';
	document.head.appendChild(meta);
	setTheme('light');
	window.localStorage.removeItem(THEME_STORAGE_KEY);
});

afterEach(() => {
	setTheme('light');
	restoreStorage();
	meta.remove();
});

function storageEvent() {
	act(() => {
		window.dispatchEvent(new StorageEvent('storage', { key: THEME_STORAGE_KEY }));
	});
}

describe('theme', () => {
	it('is light with nothing stored', () => {
		expect(activeTheme()).toBe('light');
		expect(document.documentElement.dataset.theme).toBe('light');
	});

	it('stores a switch and repaints the document and browser chrome', () => {
		setTheme('dark');
		expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');
		expect(document.documentElement.dataset.theme).toBe('dark');
		expect(meta.content).toBe('#0E1A1D');

		setTheme('light');
		expect(document.documentElement.dataset.theme).toBe('light');
		expect(meta.content).toBe('#F5F7F7');
	});

	it('re-renders subscribers on a switch', () => {
		const { result } = renderHook(() => useTheme());
		expect(result.current).toBe('light');
		act(() => setTheme('dark'));
		expect(result.current).toBe('dark');
	});

	it('follows a switch made in another tab', () => {
		window.localStorage.setItem(THEME_STORAGE_KEY, 'dark');
		storageEvent();
		expect(activeTheme()).toBe('dark');
		expect(document.documentElement.dataset.theme).toBe('dark');
	});

	it('still switches when storage refuses the write', () => {
		useStorage({ failWrites: true });
		setTheme('dark');
		expect(activeTheme()).toBe('dark');
		expect(document.documentElement.dataset.theme).toBe('dark');
	});

	it('ignores an unrecognised stored value', () => {
		window.localStorage.setItem(THEME_STORAGE_KEY, 'sepia');
		storageEvent();
		expect(activeTheme()).toBe('light');
	});
});
