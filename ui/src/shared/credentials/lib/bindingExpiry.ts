/**
 * How a binding's end date reads wherever the binding is shown: "Expires in
 * 3 days", "Expired 2 days ago", or nothing for a binding that never expires.
 *
 * The server reports `expired` as of its read; a binding whose `expires_at`
 * passes while the page is open is expired too, so both count. Time left is
 * rounded to the nearest whole unit, so access just set for 7 days reads
 * "Expires in 7 days", not 6; time since an end is rounded down, like every
 * other "ago" in the console. Where the exact moment matters, it is shown too.
 */
import type { BindingDuration } from '@/shared/credentials/lib/bindingDuration';
import {
	bindingDurationError,
	expiresAtFromDuration,
} from '@/shared/credentials/lib/bindingDuration';
import { idleLabel } from '@/shared/credentials/lib/idleLimit';

const MINUTE_MS = 60_000;
const HOUR_MS = 3_600_000;
const DAY_MS = 86_400_000;

/**
 * Within this long of its end, a binding's end date reads as a caution: the
 * same 48 hours the owner is warned before an idle limit (D13). A window as
 * long as a common duration (a week, say) would mark every binding of that
 * length as ending soon from the moment it was made.
 */
export const EXPIRES_SOON_MS = 2 * DAY_MS;

/** What a binding carries about when it ends, as the API reports it. */
export interface BindingExpiryFields {
	/** RFC 3339; null when it never expires, undefined when not reported. */
	expiresAt?: string | null;
	/** The server's verdict as of its read. */
	expired?: boolean;
	/** When the idle limit ends it if it goes unused (RFC 3339); null without one. */
	idleExpiresAt?: string | null;
	/** Which limit ended it, as the server reported it (`end_date`, `idle`). */
	expiredReason?: string | null;
	/** The idle limit, in seconds; null without one. */
	maxIdlePeriod?: number | null;
	/** How many calls it allows (1 is single use); null without a count. */
	maxQuota?: number | null;
	/** How many calls it still allows; null without a count. */
	quotaRemaining?: number | null;
}

type EndKind = 'end_date' | 'idle';

/** Which limit ended a binding: a time-based one, or its use count. */
export type BindingEndReason = EndKind | 'uses';

/** The binding's use count, when it has one: how many calls in all, and how many left. */
function usesOf(binding: BindingExpiryFields): { max: number; left: number } | null {
	if (binding.maxQuota == null) return null;
	return { max: binding.maxQuota, left: Math.max(0, binding.quotaRemaining ?? 0) };
}

function usesExhausted(binding: BindingExpiryFields): boolean {
	const uses = usesOf(binding);
	return uses !== null && uses.left <= 0;
}

/**
 * "Single use: not used yet" or "3 of 5 uses left" while a binding with a use
 * count holds; null without a count or once it is used up.
 */
export function bindingUsesLabel(binding: BindingExpiryFields): string | null {
	const uses = usesOf(binding);
	if (uses === null || uses.left <= 0) return null;
	if (uses.max === 1) return 'Single use: not used yet';
	return `${uses.left} of ${uses.max} uses left`;
}

/** "Expired: already used once" — why a used-up binding ended. */
function usesEndedLabel(binding: BindingExpiryFields): string {
	const uses = usesOf(binding);
	if (uses?.max === 1) return 'Expired: already used once';
	return uses ? `Expired: all ${uses.max} uses taken` : 'Expired: no uses left';
}

/** The limit that ends the binding first, and when; null when it never ends. */
function firstEnd(binding: BindingExpiryFields): { ms: number; kind: EndKind } | null {
	const ends = [
		{ ms: endMs(binding.expiresAt), kind: 'end_date' as const },
		{ ms: endMs(binding.idleExpiresAt), kind: 'idle' as const },
	].filter((e): e is { ms: number; kind: EndKind } => e.ms !== null);
	return ends.reduce<{ ms: number; kind: EndKind } | null>(
		(first, e) => (first === null || e.ms < first.ms ? e : first),
		null,
	);
}

function endMs(expiresAt: string | null | undefined): number | null {
	if (!expiresAt) return null;
	const ms = Date.parse(expiresAt);
	return Number.isNaN(ms) ? null : ms;
}

function plural(n: number, unit: string): string {
	return `${n} ${unit}${n === 1 ? '' : 's'}`;
}

type SpanUnit = 'day' | 'hour' | 'minute';

/**
 * A span as a count of its largest unit, or null under one minute. Rounded to
 * the nearest (`round`) or down: a rounding that reaches the next unit up (60
 * minutes, 24 hours) is read in that unit.
 */
function spanParts(ms: number, round: boolean): { n: number; unit: SpanUnit } | null {
	const to = (unitMs: number): number => (round ? Math.round : Math.floor)(ms / unitMs);
	const minutes = to(MINUTE_MS);
	if (minutes < 1) return null;
	if (minutes < 60) return { n: minutes, unit: 'minute' };
	const hours = to(HOUR_MS);
	if (hours < 24) return { n: hours, unit: 'hour' };
	return { n: to(DAY_MS), unit: 'day' };
}

/** "3 days", "5 hours", "12 minutes", or null under a minute. */
function span(ms: number, round: boolean): string | null {
	const parts = spanParts(ms, round);
	return parts ? plural(parts.n, parts.unit) : null;
}

/** "3d", "5h", "12m", or "<1m". */
function shortSpan(ms: number, round: boolean): string {
	const parts = spanParts(ms, round);
	return parts ? `${parts.n}${parts.unit[0]}` : '<1m';
}

/** Under the duration control where a binding is created, while nothing is chosen. */
export const NO_DURATION_HINT =
	'Choose when this access ends. To give access with no end date, pick Custom… and then No expiry.';

/** Whether the binding no longer authorizes calls because one of its limits was reached. */
export function bindingIsExpired(binding: BindingExpiryFields, now: number = Date.now()): boolean {
	if (binding.expired || usesExhausted(binding)) return true;
	const end = firstEnd(binding);
	return end !== null && end.ms <= now;
}

/** Which limit ended the binding (the server's word when it gave one); null while it holds. */
export function bindingExpiredReason(
	binding: BindingExpiryFields,
	now: number = Date.now(),
): BindingEndReason | null {
	if (!bindingIsExpired(binding, now)) return null;
	if (
		binding.expiredReason === 'end_date' ||
		binding.expiredReason === 'idle' ||
		binding.expiredReason === 'uses'
	) {
		return binding.expiredReason;
	}
	const first = firstEnd(binding);
	if (first !== null && first.ms <= now) return first.kind;
	return usesExhausted(binding) ? 'uses' : 'end_date';
}

/** Whether the binding ends within {@link EXPIRES_SOON_MS} and hasn't yet. */
export function bindingExpiresSoon(
	binding: BindingExpiryFields,
	now: number = Date.now(),
): boolean {
	const end = firstEnd(binding);
	return end !== null && !bindingIsExpired(binding, now) && end.ms - now <= EXPIRES_SOON_MS;
}

/** "Expired: unused for 14 days" — why an idle-ended binding ended. */
function idleEndedLabel(binding: BindingExpiryFields): string {
	return binding.maxIdlePeriod
		? `Expired: unused for ${idleLabel(binding.maxIdlePeriod)}`
		: 'Expired: unused too long';
}

/**
 * "Expires in 3 days" / "Expires in less than a minute" / "Expired 2 days ago"
 * / "Expired just now", or null when the binding never expires (or the server
 * didn't say), so nothing is claimed. A binding with a use count adds how
 * many are left ("Expires in 3 days · Single use: not used yet"), or says it
 * was used up ("Expired: already used once").
 */
export function bindingExpiryLabel(
	binding: BindingExpiryFields,
	now: number = Date.now(),
): string | null {
	const reason = bindingExpiredReason(binding, now);
	if (reason === 'uses') return usesEndedLabel(binding);
	if (reason === 'idle') return idleEndedLabel(binding);
	const time = timeLabel(binding, now);
	const uses = reason === null ? bindingUsesLabel(binding) : null;
	return time && uses ? `${time} · ${uses}` : (time ?? uses);
}

function timeLabel(binding: BindingExpiryFields, now: number): string | null {
	const first = firstEnd(binding);
	if (first?.kind === 'idle') {
		const left = span(first.ms - now, true);
		return left ? `Ends in ${left} unless used` : 'Ends in less than a minute unless used';
	}
	const end = endMs(binding.expiresAt);
	if (end === null) return binding.expired ? 'Expired' : null;
	if (bindingIsExpired(binding, now)) {
		const ago = span(now - end, false);
		return ago ? `Expired ${ago} ago` : 'Expired just now';
	}
	const left = span(end - now, true);
	return left ? `Expires in ${left}` : 'Expires in less than a minute';
}

/**
 * The compact form for tight spots: "Ends in 3d" / "Ended 2d ago", or null as
 * {@link bindingExpiryLabel}. Paired with the full phrase in the element's
 * accessible name or tooltip.
 */
export function bindingExpiryShortLabel(
	binding: BindingExpiryFields,
	now: number = Date.now(),
): string | null {
	const reason = bindingExpiredReason(binding, now);
	if (reason === 'uses') return 'Ended: used';
	if (reason === 'idle') return 'Ended: unused';
	const first = firstEnd(binding);
	if (first?.kind === 'idle') return `Ends in ${shortSpan(first.ms - now, true)} if idle`;
	const end = endMs(binding.expiresAt);
	if (end === null) {
		if (binding.expired) return 'Ended';
		const uses = usesOf(binding);
		if (uses === null) return null;
		return uses.max === 1 ? 'Single use' : `${uses.left} uses left`;
	}
	if (bindingIsExpired(binding, now)) return `Ended ${shortSpan(now - end, false)} ago`;
	return `Ends in ${shortSpan(end - now, true)}`;
}

/**
 * Whether a complete duration is chosen — a preset, a valid custom amount, or
 * "No expiry". Creating or renewing a binding waits for one: there is no
 * default, so an end date (or deliberately none) is always the operator's call.
 */
export function durationChosen(
	duration: BindingDuration | null,
	maxSeconds: number | null = null,
): boolean {
	return (
		expiresAtFromDuration(duration) !== null &&
		bindingDurationError(duration, maxSeconds) === null
	);
}

/**
 * Whether the choice is "No expiry": saving it creates a binding with no end
 * date, or clears an existing one's. Either asks for confirmation first.
 */
export function choosesNoExpiry(duration: BindingDuration | null): boolean {
	return expiresAtFromDuration(duration) === 'never';
}
