/**
 * Comparator: most recently created first, by an ISO-8601 `createdAt`.
 *
 * Shared by the Library's docked "Your workspace" panel and the expanded
 * Workspace grid so both list APIs in the same order (newest import on top).
 * Rows with an unparseable timestamp sink to the bottom; ties fall back to
 * `tiebreak` (e.g. a title compare) so the order is stable across refetches.
 */
export function newestFirst<T extends { createdAt: string }>(
	tiebreak?: (a: T, b: T) => number,
): (a: T, b: T) => number {
	return (a, b) => {
		const ta = Date.parse(a.createdAt);
		const tb = Date.parse(b.createdAt);
		const va = Number.isNaN(ta) ? -Infinity : ta;
		const vb = Number.isNaN(tb) ? -Infinity : tb;
		if (va !== vb) return vb > va ? 1 : -1;
		return tiebreak ? tiebreak(a, b) : 0;
	};
}
