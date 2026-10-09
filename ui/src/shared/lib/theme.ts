/**
 * Theme — light and dark, resolved once before first paint.
 *
 * A choice stored in this browser wins; with nothing stored the app is light.
 * The OS appearance is not consulted — light is the product default, and dark
 * is an opt-in the user picks from the user menu.
 *
 * Switching swaps the `data-theme` attribute on `<html>` — no reload. The
 * palettes live in `index.css`: `:root` is dark and `[data-theme='light']`
 * overrides the same tokens, so this module owns no colours.
 *
 * `main.tsx` imports this module first, for its side effect, so the attribute
 * lands before the app renders.
 */
import { useSyncExternalStore } from 'react';

export type Theme = 'light' | 'dark';

export const THEME_STORAGE_KEY = 'jentic-one.theme';

/** The palette for a browser with no stored choice. */
const DEFAULT_THEME: Theme = 'light';

/** The browser chrome colour per theme (`<meta name="theme-color">`). */
const CHROME_COLOR: Record<Theme, string> = { dark: '#0E1A1D', light: '#F5F7F7' };

function storedTheme(): Theme | null {
	try {
		const v = window.localStorage.getItem(THEME_STORAGE_KEY);
		return v === 'light' || v === 'dark' ? v : null;
	} catch {
		return null;
	}
}

/** The theme in force: this browser's stored choice, else the default. */
export function activeTheme(): Theme {
	return storedTheme() ?? DEFAULT_THEME;
}

const listeners = new Set<() => void>();

function paint(): void {
	const theme = activeTheme();
	document.documentElement.dataset.theme = theme;
	document
		.querySelector('meta[name="theme-color"]')
		?.setAttribute('content', CHROME_COLOR[theme]);
	for (const listener of listeners) listener();
}

/** Store a choice for this browser and repaint. */
export function setTheme(theme: Theme): void {
	try {
		window.localStorage.setItem(THEME_STORAGE_KEY, theme);
	} catch {
		/* storage unavailable — the choice lasts for this page only */
	}
	paint();
}

function subscribe(onChange: () => void): () => void {
	listeners.add(onChange);
	return () => {
		listeners.delete(onChange);
	};
}

/** The theme in force, re-rendering the caller on a switch. */
export function useTheme(): Theme {
	return useSyncExternalStore(subscribe, activeTheme);
}

if (typeof window !== 'undefined') paint();
