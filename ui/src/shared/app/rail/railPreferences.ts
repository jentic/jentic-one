/**
 * Activity rail preferences — toast scope + sound, persisted to localStorage.
 *
 * Two rail bodies can be mounted at once (the docked `xl+` rail is CSS-hidden,
 * not unmounted, when the drawer opens below `xl`), so each preference
 * broadcasts a window event and every subscriber re-reads it — flipping sound
 * in the drawer is reflected in the docked rail and vice versa.
 */
import { useCallback, useEffect, useState } from 'react';
import {
	RAIL_AUDIO_STORAGE_KEY,
	TOAST_SCOPE_CHANGE_EVENT,
	readToastScope,
	writeToastScope,
} from '@/shared/lib/agentStream';
import type { ToastScope } from '@/shared/lib/agentStream';

const RAIL_AUDIO_CHANGE_EVENT = 'j1:rail-audio-change';

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
export function readAudioOnCritical(): boolean {
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
