/**
 * A binding's idle limit, as the console chooses it: the binding ends once it
 * goes this long without a call. Off by default; when on, a short list of
 * idle-sized presets (7, 14, 30 and 90 days) plus "Custom…" in hours or days.
 *
 * The shortest limit is 1 day: the broker records use about once an hour, so
 * a shorter limit couldn't be honoured exactly (the server refuses one with
 * 422). The longest is 999 days.
 */
const END_FORMAT = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });

export type IdlePreset = '7d' | '14d' | '30d' | '90d';
export type IdleUnit = 'hours' | 'days';

export type IdleLimit =
	| { kind: 'preset'; preset: IdlePreset }
	| {
			kind: 'custom';
			/** The number field's text. */
			amount: string;
			unit: IdleUnit;
	  };

const HOUR_S = 3_600;
const DAY_S = 86_400;

export const MIN_IDLE_SECONDS = DAY_S;
export const MAX_IDLE_SECONDS = 999 * DAY_S;

export const IDLE_PRESETS: readonly { value: IdlePreset; label: string; seconds: number }[] = [
	{ value: '7d', label: '7 days', seconds: 7 * DAY_S },
	{ value: '14d', label: '14 days', seconds: 14 * DAY_S },
	{ value: '30d', label: '30 days', seconds: 30 * DAY_S },
	{ value: '90d', label: '90 days', seconds: 90 * DAY_S },
];

export const IDLE_UNITS: readonly { value: IdleUnit; label: string }[] = [
	{ value: 'hours', label: 'Hours' },
	{ value: 'days', label: 'Days' },
];

/** What turning the idle row on starts with. */
export const DEFAULT_IDLE_LIMIT: IdleLimit = { kind: 'preset', preset: '14d' };

const UNIT_S: Record<IdleUnit, number> = { hours: HOUR_S, days: DAY_S };

/** The limit in whole seconds, or `null` while a custom amount is incomplete or out of range. */
export function idleSeconds(limit: IdleLimit | null): number | null {
	if (limit === null) return null;
	if (limit.kind === 'preset') {
		return IDLE_PRESETS.find((p) => p.value === limit.preset)?.seconds ?? null;
	}
	const trimmed = limit.amount.trim();
	if (!/^\d+$/.test(trimmed)) return null;
	const seconds = Number(trimmed) * UNIT_S[limit.unit];
	return seconds >= MIN_IDLE_SECONDS && seconds <= MAX_IDLE_SECONDS ? seconds : null;
}

/** What is wrong with the limit, for the field's error; `null` when it is fine or off. */
export function idleLimitError(
	limit: IdleLimit | null,
	maxSeconds: number | null = null,
): string | null {
	if (limit === null) return null;
	if (limit.kind === 'custom') {
		if (limit.amount.trim() === '') return 'Enter how long it may go unused.';
		if (!/^\d+$/.test(limit.amount.trim())) return 'Enter a whole number.';
		if (idleSeconds(limit) === null) return 'Choose between 1 day and 999 days.';
	}
	const seconds = idleSeconds(limit);
	if (maxSeconds !== null && seconds !== null && seconds > maxSeconds) {
		return `This org allows an idle limit of at most ${idleLabel(maxSeconds)}.`;
	}
	return null;
}

/** A limit in seconds (as the API returns it) as the field's choice. */
export function idleLimitFromSeconds(seconds: number | null | undefined): IdleLimit | null {
	if (!seconds || seconds < MIN_IDLE_SECONDS) return null;
	const preset = IDLE_PRESETS.find((p) => p.seconds === seconds);
	if (preset) return { kind: 'preset', preset: preset.value };
	if (seconds % DAY_S === 0)
		return { kind: 'custom', amount: String(seconds / DAY_S), unit: 'days' };
	return { kind: 'custom', amount: String(Math.round(seconds / HOUR_S)), unit: 'hours' };
}

/** "14 days", "36 hours", "1 day". */
export function idleLabel(seconds: number): string {
	if (seconds % DAY_S === 0) {
		const days = seconds / DAY_S;
		return `${days} day${days === 1 ? '' : 's'}`;
	}
	const hours = Math.round(seconds / HOUR_S);
	return `${hours} hour${hours === 1 ? '' : 's'}`;
}

/**
 * One line saying how the chosen limits combine, if saved now: the end date,
 * the idle limit, both, or — with neither — null (the "No expiry" warning
 * covers that). An idle limit alone has no end date: an agent that keeps
 * using the access keeps it.
 */
export function describePolicy(end: Date | 'never' | null, idle: number | null): string | null {
	const endText = end instanceof Date ? END_FORMAT.format(end) : null;
	if (idle === null) return endText ? `Access ends around ${endText}.` : null;
	if (endText)
		return `Access ends around ${endText}, or sooner if unused for ${idleLabel(idle)}.`;
	return `Access ends if unused for ${idleLabel(idle)}. An agent that keeps using it keeps access.`;
}

/**
 * The one line under a grant's limits saying when access would end if saved
 * now: every limit chosen (end date, idle limit, use count) combined, "No
 * expiry: …" for no limit at all, or `emptyHint` while no end date is chosen.
 */
export function describeAccess(
	end: Date | 'never' | null,
	idle: number | null,
	uses: number | null,
	emptyHint: string | null = null,
): string | null {
	if (uses !== null) return describeSingleUse(uses, end, idle);
	const policy = describePolicy(end, idle);
	if (policy) return policy;
	if (end === 'never') return 'No expiry: access lasts until someone removes it.';
	return emptyHint;
}

/**
 * How a use count combines with the other limits, if saved now: "Access ends
 * after one use, or around 15 Dec if not used by then." Any limit reached
 * ends the access.
 */
export function describeSingleUse(
	count: number,
	end: Date | 'never' | null,
	idle: number | null,
): string {
	const uses = count === 1 ? 'one use' : `${count} uses`;
	const endText = end instanceof Date ? END_FORMAT.format(end) : null;
	if (endText && idle !== null)
		return `Access ends after ${uses}, around ${endText}, or sooner if unused for ${idleLabel(idle)}.`;
	if (endText) return `Access ends after ${uses}, or around ${endText} if not used by then.`;
	if (idle !== null) return `Access ends after ${uses}, or if unused for ${idleLabel(idle)}.`;
	return `Access ends after ${uses}.`;
}
