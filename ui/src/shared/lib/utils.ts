import { clsx, type ClassValue } from 'clsx';
import { extendTailwindMerge } from 'tailwind-merge';

// Teach the merger the custom radius tokens (`rounded-field`, `rounded-panel`)
// so a call-site `rounded-full` still overrides a primitive's `rounded-field`.
const twMerge = extendTailwindMerge({
	extend: { theme: { radius: ['field', 'panel'] } },
});

export function cn(...inputs: ClassValue[]) {
	return twMerge(clsx(inputs));
}

/**
 * Compact relative time ("3s", "5m", "2h", "4d") from an ISO string, epoch
 * milliseconds, or epoch seconds. Returns "—" for missing/invalid input and
 * "now" for sub-second deltas. Used for activity/age columns across surfaces.
 */
export function timeAgo(value: string | number | null | undefined): string {
	if (value == null) return '—';
	let ms: number;
	if (typeof value === 'number') {
		// Heuristic: treat 10-digit values as epoch seconds, else milliseconds.
		ms = value < 1e12 ? value * 1000 : value;
	} else {
		ms = Date.parse(value);
	}
	if (Number.isNaN(ms)) return '—';

	const deltaSec = Math.round((Date.now() - ms) / 1000);
	if (deltaSec < 1) return 'now';
	if (deltaSec < 60) return `${deltaSec}s`;
	const min = Math.floor(deltaSec / 60);
	if (min < 60) return `${min}m`;
	const hr = Math.floor(min / 60);
	if (hr < 24) return `${hr}h`;
	const days = Math.floor(hr / 24);
	if (days < 30) return `${days}d`;
	const months = Math.floor(days / 30);
	if (months < 12) return `${months}mo`;
	return `${Math.floor(months / 12)}y`;
}

/**
 * Absolute, locale-aware timestamp ("Jun 19, 2026, 8:30 PM") from an ISO string.
 * Returns "—" for missing/invalid input. Used in tooltips and meta grids where a
 * precise time is preferred over the compact relative form.
 */
export function formatTimestamp(value: string | null | undefined): string {
	if (!value) return '—';
	const ms = Date.parse(value);
	if (Number.isNaN(ms)) return '—';
	return new Date(ms).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}
