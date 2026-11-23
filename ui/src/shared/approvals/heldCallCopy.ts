/**
 * The words the attention surfaces use for a held call — the bell, the rail's
 * Inbox and the attention toast — so a call reads the same on each:
 * "billing-ops wants to run “Create a charge”", "5m ago", "expires in 23 h".
 */
import { isRouteHeadline } from '@/shared/approvals/heldCall';

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

/**
 * The headline as the object of "wants to run …": an operation's name in quotes
 * (“Flight delays”, so a noun phrase or a verb phrase reads the same), a route
 * as it is (POST /v1/charges).
 */
export function askPhrase(headline: string): string {
	return isRouteHeadline(headline) ? headline : `“${headline}”`;
}

/**
 * `text` without explicit bidi controls (U+202A–U+202E embeddings and
 * overrides, U+2066–U+2069 isolates): a name can't reorder the words around
 * it. Right-to-left text needs none of them; `dir="auto"` reads it right.
 */
export function stripBidiControls(text: string): string {
	return text.replace(/[\u202A-\u202E\u2066-\u2069]/g, '');
}

/** `billing-ops wants to run “Create a charge”`. */
export function heldCallAsk(agentName: string, headline: string): string {
	return `${stripBidiControls(agentName)} wants to run ${askPhrase(stripBidiControls(headline))}`;
}

/**
 * "just now", "5m ago", "2h ago", "3d ago": how long ago `iso` was — a call made,
 * a decision taken. The one relative-time reader of the held-call surfaces.
 */
export function waitingSince(iso: string, now: number = Date.now()): string {
	const at = Date.parse(iso);
	if (Number.isNaN(at)) return '';
	const ms = Math.max(0, now - at);
	if (ms < MINUTE_MS) return 'just now';
	if (ms < HOUR_MS) return `${Math.floor(ms / MINUTE_MS)}m ago`;
	if (ms < DAY_MS) return `${Math.floor(ms / HOUR_MS)}h ago`;
	return `${Math.floor(ms / DAY_MS)}d ago`;
}

/**
 * "expires in 23 h", "expires in 40 min", "expires in 3 days", "expired": what
 * is left of the window, in the units the review pop-up uses.
 */
export function expiresIn(expiresAt: string, now: number = Date.now()): string {
	const at = Date.parse(expiresAt);
	if (Number.isNaN(at)) return '';
	if (at <= now) return 'expired';
	// A window still open never reads as closed: under a minute rounds up.
	const minutes = Math.max(1, Math.round((at - now) / MINUTE_MS));
	if (minutes < 60) return `expires in ${minutes} min`;
	const hours = Math.floor(minutes / 60);
	if (hours < 48) return `expires in ${hours} h`;
	const days = Math.floor(hours / 24);
	return days > 365 ? 'expires in over a year' : `expires in ${days} days`;
}
