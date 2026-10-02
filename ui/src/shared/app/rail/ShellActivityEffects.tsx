/**
 * ShellActivityEffects — the activity side-effects that must run on EVERY
 * page, so they live in the shell rather than in the rail (which is hidden on
 * Monitor, where the page itself shows the stream):
 *
 *   • the opt-in sound on failures — org-wide like the failure toasts, and
 *     mounted once so the docked rail and the drawer never double-beep. The
 *     switch lives in the Notifications settings.
 */
import { useEffect, useRef } from 'react';
import { useRailPreferences } from '@/shared/app/rail/railPreferences';
import { playCriticalCue } from '@/shared/lib/audioCue';
import { isFailureSeverity, useAgentStream } from '@/shared/lib/agentStream';

/** Pages whose content already is the activity stream — no rail beside them. */
export function isRailHiddenOn(pathname: string): boolean {
	return pathname.startsWith('/monitor');
}

export function ShellActivityEffects() {
	const { latest } = useAgentStream();
	const { audioOnCritical } = useRailPreferences();
	const lastBeepedRef = useRef<string | null>(null);

	useEffect(() => {
		if (!audioOnCritical || !latest) return;
		if (!isFailureSeverity(latest.severity)) return;
		if (lastBeepedRef.current === latest.id) return;
		lastBeepedRef.current = latest.id;
		playCriticalCue();
	}, [latest, audioOnCritical]);

	return null;
}
