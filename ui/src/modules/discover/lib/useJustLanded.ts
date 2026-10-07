/**
 * useJustLanded — the catalog api ids whose import just settled (they left the
 * pending set), for the workspace panel's brief "just added" flash.
 *
 * Each batch that leaves the pending set owns its own removal timer: a later
 * change to the pending set (another import starting or landing) never
 * cancels an earlier batch's removal. All timers are cleared on unmount.
 */
import { useEffect, useRef, useState } from 'react';

export function useJustLanded(
	pendingApiIds: ReadonlySet<string>,
	durationMs = 2000,
): ReadonlySet<string> {
	const [justLanded, setJustLanded] = useState<ReadonlySet<string>>(() => new Set());
	const prevPendingRef = useRef<ReadonlySet<string>>(new Set());
	const timersRef = useRef(new Set<number>());

	useEffect(() => {
		const timers = timersRef.current;
		return () => {
			for (const t of timers) window.clearTimeout(t);
			timers.clear();
		};
	}, []);

	useEffect(() => {
		const landed = [...prevPendingRef.current].filter((id) => !pendingApiIds.has(id));
		prevPendingRef.current = new Set(pendingApiIds);
		if (landed.length === 0) return;
		setJustLanded((prev) => new Set([...prev, ...landed]));
		const timers = timersRef.current;
		const timer = window.setTimeout(() => {
			timers.delete(timer);
			setJustLanded((prev) => new Set([...prev].filter((id) => !landed.includes(id))));
		}, durationMs);
		timers.add(timer);
	}, [pendingApiIds, durationMs]);

	return justLanded;
}
