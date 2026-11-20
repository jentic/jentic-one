import { timeAgo } from '@/shared/lib';

/** "3m ago" from the shared compact age; "just now" under a second. */
export function ago(iso: string): string {
	const age = timeAgo(iso);
	return age === 'now' ? 'just now' : `${age} ago`;
}

/** "Used 3d ago" / "Never used" for a binding's last call; null when the server
 * didn't report it, so nothing is claimed. */
export function lastUsedLabel(lastUsedAt: string | null | undefined): string | null {
	if (lastUsedAt === undefined) return null;
	return lastUsedAt === null ? 'Never used' : `Used ${ago(lastUsedAt)}`;
}

/** Compact form of {@link lastUsedLabel} for tight spots: "Used 3d" / "Never".
 * Always a leading slice of the full label, so a card whose accessible name
 * carries the full phrase still contains the visible text (WCAG 2.5.3). */
export function lastUsedShortLabel(lastUsedAt: string | null | undefined): string | null {
	if (lastUsedAt === undefined) return null;
	if (lastUsedAt === null) return 'Never';
	const age = timeAgo(lastUsedAt);
	return age === 'now' ? 'Used just now' : `Used ${age}`;
}
