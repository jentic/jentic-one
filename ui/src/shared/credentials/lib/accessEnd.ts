/**
 * What happens around the end of a binding's access, as the console chooses
 * it below the limits: whether its owner is warned before it ends, and what a
 * call through it gets once it has ended.
 *
 * The warning (`idle_warning` on the wire) covers both deadlines: the end
 * date, for access granted for 4 days or more, and the idle limit, for idle
 * limits of 4 days or more. Each warns up to 48 hours ahead, once. Off by
 * default.
 */
/** Slack for a 4-day end date chosen a moment ago. */
const SLACK_MS = 3_600_000;

/** The shortest end date or idle limit an owner can be warned about. */
export const WARNING_MIN_SECONDS = 4 * 86_400;

/** What a call through an ended binding gets (`after_expiry`). */
export type AfterExpiry = 'hold' | 'refuse';

/** New bindings hold the first call after access ends. */
export const DEFAULT_AFTER_EXPIRY: AfterExpiry = 'hold';

export const AFTER_EXPIRY_OPTIONS: readonly {
	value: AfterExpiry;
	label: string;
	hint: string;
}[] = [
	{
		value: 'hold',
		label: 'Hold first denied request',
		hint: 'The first call after access ends waits for you to renew it.',
	},
	{
		value: 'refuse',
		label: 'Let the agent ask',
		hint: 'Calls are refused; the agent asks you to renew.',
	},
];

/** A wire value as the console's choice (anything unknown reads as the default). */
export function afterExpiryFrom(value: string | null | undefined): AfterExpiry {
	return value === 'refuse' ? 'refuse' : DEFAULT_AFTER_EXPIRY;
}

/**
 * Whether the warning is an option for the access if saved now: it has an
 * end date or an idle limit, and neither is under 4 days (an end date less
 * than 4 days away, or an idle limit shorter than 4 days, rules it out).
 */
export function expiryWarnable(
	end: Date | 'never' | 'unknown' | null,
	idleSeconds: number | null,
	now: number = Date.now(),
): boolean {
	// `'unknown'`: an end date the server will work out (a renewal that chooses
	// none runs the binding again for as long as it was granted for). It will
	// have one; whether it is long enough to warn about is the server's call.
	const hasEnd = end instanceof Date || end === 'unknown';
	if (end instanceof Date && end.getTime() - now < WARNING_MIN_SECONDS * 1000 - SLACK_MS)
		return false;
	if (idleSeconds !== null && idleSeconds < WARNING_MIN_SECONDS) return false;
	return hasEnd || idleSeconds !== null;
}

/** The `idle_warning` to send: ticked, with a deadline worth warning about. */
export function expiryWarning(
	warn: boolean,
	end: Date | 'never' | 'unknown' | null,
	idleSeconds: number | null,
): boolean {
	return warn && expiryWarnable(end, idleSeconds);
}
