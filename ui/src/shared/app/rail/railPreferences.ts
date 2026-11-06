/**
 * Rail preferences, persisted to localStorage: the toast scope and sound, and
 * the rail's own view — collapsed or open, and which tab (Inbox or Activity)
 * it shows.
 *
 * Several surfaces read the same preference at once (the docked `xl+` rail is
 * CSS-hidden, not unmounted, when the drawer opens below `xl`; the attention
 * toast watches the rail's view), so each preference broadcasts a window event
 * and every subscriber re-reads it — flipping sound in the drawer is reflected
 * in the docked rail, and the toast's "Open inbox" opens the docked rail.
 */
import { useCallback, useEffect, useState } from 'react';
import {
	RAIL_AUDIO_STORAGE_KEY,
	RAIL_COLLAPSED_STORAGE_KEY,
	TOAST_SCOPE_CHANGE_EVENT,
	readToastScope,
	writeToastScope,
} from '@/shared/lib/agentStream';
import type { ToastScope } from '@/shared/lib/agentStream';

const RAIL_AUDIO_CHANGE_EVENT = 'j1:rail-audio-change';

/** The rail's two tabs: the held calls waiting on the viewer, and the live feed. */
export type RailTab = 'inbox' | 'activity';

export const RAIL_TAB_STORAGE_KEY = 'j1.agentRail.tab';
const RAIL_VIEW_CHANGE_EVENT = 'j1:rail-view-change';

/** Detail of a view change: `focusTab` asks the docked rail to focus that tab. */
interface RailViewChange {
	focusTab?: RailTab;
}

export function readBool(key: string, fallback: boolean): boolean {
	if (typeof window === 'undefined') return fallback;
	try {
		const v = window.localStorage.getItem(key);
		return v === null ? fallback : v === '1';
	} catch {
		return fallback;
	}
}

export function writeBool(key: string, value: boolean) {
	if (typeof window === 'undefined') return;
	try {
		window.localStorage.setItem(key, value ? '1' : '0');
	} catch {
		/* ignore */
	}
}

/** Sound on failures. Defaults OFF; an explicitly stored choice is respected. */
function readAudioOnCritical(): boolean {
	return readBool(RAIL_AUDIO_STORAGE_KEY, false);
}

export function useRailPreferences() {
	const [toastScope, setToastScopeState] = useState<ToastScope>(() => readToastScope());
	const [audioOnCritical, setAudioState] = useState<boolean>(() => readAudioOnCritical());

	useEffect(() => {
		const onScope = () => setToastScopeState(readToastScope());
		const onAudio = () => setAudioState(readAudioOnCritical());
		window.addEventListener(TOAST_SCOPE_CHANGE_EVENT, onScope);
		window.addEventListener(RAIL_AUDIO_CHANGE_EVENT, onAudio);
		return () => {
			window.removeEventListener(TOAST_SCOPE_CHANGE_EVENT, onScope);
			window.removeEventListener(RAIL_AUDIO_CHANGE_EVENT, onAudio);
		};
	}, []);

	const setToastScope = useCallback((scope: ToastScope) => {
		setToastScopeState(scope);
		writeToastScope(scope);
	}, []);

	const setAudioOnCritical = useCallback((on: boolean) => {
		setAudioState(on);
		writeBool(RAIL_AUDIO_STORAGE_KEY, on);
		window.dispatchEvent(new CustomEvent(RAIL_AUDIO_CHANGE_EVENT));
	}, []);

	return { toastScope, setToastScope, audioOnCritical, setAudioOnCritical };
}

/** The last tab the viewer chose; Activity until they pick one. */
function readRailTab(): RailTab {
	if (typeof window === 'undefined') return 'activity';
	try {
		return window.localStorage.getItem(RAIL_TAB_STORAGE_KEY) === 'inbox' ? 'inbox' : 'activity';
	} catch {
		return 'activity';
	}
}

function writeRailTab(tab: RailTab) {
	if (typeof window === 'undefined') return;
	try {
		window.localStorage.setItem(RAIL_TAB_STORAGE_KEY, tab);
	} catch {
		/* ignore */
	}
}

/** Collapsed by default: the rail is an opt-in surface, not page chrome. */
function readRailCollapsed(): boolean {
	return readBool(RAIL_COLLAPSED_STORAGE_KEY, true);
}

function broadcastRailView(change: RailViewChange = {}) {
	window.dispatchEvent(
		new CustomEvent<RailViewChange>(RAIL_VIEW_CHANGE_EVENT, { detail: change }),
	);
}

/**
 * Open the rail on `tab` from anywhere in the shell (the attention toast, the
 * `?inbox=1` link). With `focus`, the docked rail moves keyboard focus to that
 * tab once it is on screen — the control that asked is usually gone by then.
 */
export function openRailOn(tab: RailTab, { focus = false }: { focus?: boolean } = {}) {
	writeRailTab(tab);
	writeBool(RAIL_COLLAPSED_STORAGE_KEY, false);
	broadcastRailView(focus ? { focusTab: tab } : {});
}

/**
 * The rail's view: whether it is collapsed to the strip and which tab it
 * shows. Shared by the docked rail, the drawer and the attention toast.
 */
export function useRailView() {
	const [collapsed, setCollapsedState] = useState<boolean>(() => readRailCollapsed());
	const [tab, setTabState] = useState<RailTab>(() => readRailTab());

	useEffect(() => {
		const onChange = () => {
			setCollapsedState(readRailCollapsed());
			setTabState(readRailTab());
		};
		window.addEventListener(RAIL_VIEW_CHANGE_EVENT, onChange);
		return () => window.removeEventListener(RAIL_VIEW_CHANGE_EVENT, onChange);
	}, []);

	const setCollapsed = useCallback((next: boolean) => {
		setCollapsedState(next);
		writeBool(RAIL_COLLAPSED_STORAGE_KEY, next);
		broadcastRailView();
	}, []);

	const setTab = useCallback((next: RailTab) => {
		setTabState(next);
		writeRailTab(next);
		broadcastRailView();
	}, []);

	return { collapsed, setCollapsed, tab, setTab };
}

/** Run `onFocusTab` whenever something asks the rail to focus one of its tabs. */
export function useRailFocusRequests(onFocusTab: (tab: RailTab) => void) {
	useEffect(() => {
		const onChange = (event: Event) => {
			const tab = (event as CustomEvent<RailViewChange>).detail?.focusTab;
			if (tab) onFocusTab(tab);
		};
		window.addEventListener(RAIL_VIEW_CHANGE_EVENT, onChange);
		return () => window.removeEventListener(RAIL_VIEW_CHANGE_EVENT, onChange);
	}, [onFocusTab]);
}
