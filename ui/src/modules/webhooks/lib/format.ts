/** Time / number formatting helpers local to the webhooks module. */

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** Compact relative time — "just now", "12m ago", "3h ago", "5d ago". */
export function timeAgo(iso: string | null): string {
	if (!iso) return '—';
	const delta = Date.now() - Date.parse(iso);
	if (delta < MINUTE) return 'just now';
	if (delta < HOUR) return `${Math.floor(delta / MINUTE)}m ago`;
	if (delta < DAY) return `${Math.floor(delta / HOUR)}h ago`;
	return `${Math.floor(delta / DAY)}d ago`;
}

/** "in 28m" style countdown for a pending retry. */
export function timeUntil(iso: string | null): string {
	if (!iso) return '—';
	const delta = Date.parse(iso) - Date.now();
	if (delta <= 0) return 'now';
	if (delta < HOUR) return `in ${Math.max(1, Math.round(delta / MINUTE))}m`;
	if (delta < DAY) return `in ${Math.round(delta / HOUR)}h`;
	return `in ${Math.round(delta / DAY)}d`;
}

export function formatDateTime(iso: string | null): string {
	return iso ? new Date(iso).toLocaleString() : '—';
}

/** 0–1 ratio → "98%" (or em-dash when unknown). */
export function formatRate(rate: number | null): string {
	return rate == null ? '—' : `${Math.round(rate * 100)}%`;
}

/** Truncate a URL for table display, masking any query string. */
export function displayUrl(url: string): string {
	try {
		const u = new URL(url);
		const base = `${u.host}${u.pathname}`;
		const masked = u.search ? `${base}?…` : base;
		return masked.length > 48 ? `${masked.slice(0, 47)}…` : masked;
	} catch {
		return url;
	}
}

/** Pretty-print an unknown JSON payload for the inspector panes. */
export function prettyJson(value: unknown): string {
	try {
		return JSON.stringify(value, null, 2);
	} catch {
		return String(value);
	}
}
