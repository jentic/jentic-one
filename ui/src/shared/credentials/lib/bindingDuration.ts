/**
 * How long access lasts — a grant's or an agent's `expires_at`, chosen as a
 * duration rather than a timestamp (credential grant expiry, D8).
 *
 * The choice is one of a short list of preset durations (1 hour to 90 days),
 * or "Custom…": a whole number and a unit, where "No expiry" also lives. "No
 * expiry" is deliberately not a preset, so choosing it takes a deliberate
 * step, and nothing is chosen until the operator picks: there is no default.
 *
 * A custom amount is kept as the field's text so a half-typed value ("", "1")
 * stays editable; it is parsed only when the expiry is worked out. The expiry
 * is computed at the moment of saving, never ahead of it, so "1 day" runs from
 * when the operator confirms, not from when the control rendered.
 *
 * Every unit is a fixed length of time. There are deliberately no months (or
 * years): "1 month" depends on the start date and the calendar, which makes the
 * resulting expiry ambiguous; operators wanting about a month enter 30 days.
 */

import { limitLabel } from '@/shared/credentials/lib/grantLimits';

export type BindingDurationUnit = 'seconds' | 'minutes' | 'hours' | 'days';

/** A custom picker's unit: a fixed length, or `never` (no expiry). */
export type CustomDurationUnit = BindingDurationUnit | 'never';

export type BindingDurationPreset = '1h' | '8h' | '1d' | '7d' | '30d' | '90d';

export type BindingDuration =
	| { kind: 'preset'; preset: BindingDurationPreset }
	| {
			kind: 'custom';
			/** The number field's text. */
			amount: string;
			unit: CustomDurationUnit;
	  };

const HOUR_MS = 3_600_000;
const DAY_MS = 86_400_000;

export const BINDING_DURATION_PRESETS: readonly {
	value: BindingDurationPreset;
	label: string;
	ms: number;
}[] = [
	{ value: '1h', label: '1 hour', ms: HOUR_MS },
	{ value: '8h', label: '8 hours', ms: 8 * HOUR_MS },
	{ value: '1d', label: '1 day', ms: DAY_MS },
	{ value: '7d', label: '7 days', ms: 7 * DAY_MS },
	{ value: '30d', label: '30 days', ms: 30 * DAY_MS },
	{ value: '90d', label: '90 days', ms: 90 * DAY_MS },
];

export const CUSTOM_DURATION_UNITS: readonly { value: CustomDurationUnit; label: string }[] = [
	{ value: 'seconds', label: 'Seconds' },
	{ value: 'minutes', label: 'Minutes' },
	{ value: 'hours', label: 'Hours' },
	{ value: 'days', label: 'Days' },
	{ value: 'never', label: 'No expiry' },
];

/** Where "Custom…" starts: a day, so switching to it never lands on "No expiry". */
export const INITIAL_CUSTOM_DURATION: BindingDuration = {
	kind: 'custom',
	amount: '1',
	unit: 'days',
};

/** Upper bound on a custom amount, whatever the unit (999 days is about 2.7 years). */
export const MAX_BINDING_DURATION_AMOUNT = 999;

const UNIT_MS: Record<BindingDurationUnit, number> = {
	seconds: 1_000,
	minutes: 60_000,
	hours: HOUR_MS,
	days: DAY_MS,
};

/** The amount as a whole number in range, or `null` when it is not one. */
export function parseDurationAmount(amount: string): number | null {
	const trimmed = amount.trim();
	if (!/^\d+$/.test(trimmed)) return null;
	const n = Number(trimmed);
	return n >= 1 && n <= MAX_BINDING_DURATION_AMOUNT ? n : null;
}

/**
 * What is wrong with a custom amount, for the field's error — `null` when it is
 * fine, when nothing is chosen yet, for a preset, and for "No expiry" (which
 * takes no amount).
 */
export function bindingDurationError(
	duration: BindingDuration | null,
	maxSeconds: number | null = null,
): string | null {
	if (duration === null) return null;
	if (duration.kind === 'custom' && duration.unit !== 'never') {
		if (duration.amount.trim() === '') return 'Enter how long access lasts.';
		if (parseDurationAmount(duration.amount) === null) {
			return `Enter a whole number from 1 to ${MAX_BINDING_DURATION_AMOUNT}.`;
		}
	}
	if (maxSeconds === null) return null;
	const seconds = durationSeconds(duration);
	if (seconds === 'never')
		return `This org requires access to end within ${limitLabel(maxSeconds)}.`;
	if (seconds !== null && seconds > maxSeconds) {
		return `This org allows at most ${limitLabel(maxSeconds)}.`;
	}
	return null;
}

/** The duration in whole seconds, `'never'` for no expiry, or null while incomplete. */
export function durationSeconds(duration: BindingDuration | null): number | 'never' | null {
	if (duration === null) return null;
	if (duration.kind === 'preset') {
		const preset = BINDING_DURATION_PRESETS.find((p) => p.value === duration.preset);
		return preset ? preset.ms / 1000 : null;
	}
	if (duration.unit === 'never') return 'never';
	const amount = parseDurationAmount(duration.amount);
	return amount === null ? null : (amount * UNIT_MS[duration.unit]) / 1000;
}

/**
 * When access chosen at `now` with this duration ends: a `Date`, `'never'` for
 * no expiry, or `null` when nothing is chosen yet or a custom amount is
 * invalid. Kept apart so an incomplete choice can never be mistaken for "no
 * expiry".
 */
export function expiresAtFromDuration(
	duration: BindingDuration | null,
	now: Date = new Date(),
): Date | 'never' | null {
	if (duration === null) return null;
	if (duration.kind === 'preset') {
		const preset = BINDING_DURATION_PRESETS.find((p) => p.value === duration.preset);
		return preset ? new Date(now.getTime() + preset.ms) : null;
	}
	if (duration.unit === 'never') return 'never';
	const amount = parseDurationAmount(duration.amount);
	if (amount === null) return null;
	return new Date(now.getTime() + amount * UNIT_MS[duration.unit]);
}

/**
 * The `expires_at` to send for an optional duration: an ISO 8601 string, or
 * `null` for "No expiry", or `undefined` when nothing is chosen or a custom
 * amount is invalid (callers block saving on an invalid amount separately).
 */
export function expiresAtIso(
	duration: BindingDuration | null,
	now: Date = new Date(),
): string | null | undefined {
	const end = expiresAtFromDuration(duration, now);
	return end === 'never' ? null : end?.toISOString();
}

const CUSTOM_UNITS_LARGEST_FIRST: readonly BindingDurationUnit[] = [
	'days',
	'hours',
	'minutes',
	'seconds',
];

/**
 * A duration in seconds (a connect request's ``requested_expires_in``) as the
 * control's choice: its preset when one matches exactly, otherwise "Custom…"
 * in the largest unit that divides it (90 minutes reads as 90 minutes, not
 * 1.5 hours). ``null`` for a duration the control can't hold (over 999 of
 * its unit, or not a positive whole number).
 */
export function durationFromSeconds(seconds: number): BindingDuration | null {
	if (!Number.isInteger(seconds) || seconds <= 0) return null;
	const preset = BINDING_DURATION_PRESETS.find((p) => p.ms === seconds * 1000);
	if (preset) return { kind: 'preset', preset: preset.value };
	for (const unit of CUSTOM_UNITS_LARGEST_FIRST) {
		const perUnit = UNIT_MS[unit] / 1000;
		if (seconds % perUnit === 0 && seconds / perUnit <= MAX_BINDING_DURATION_AMOUNT) {
			return { kind: 'custom', amount: String(seconds / perUnit), unit };
		}
	}
	return null;
}

/** "7 days", "90 minutes", "No expiry" — a choice in words; null while incomplete. */
export function durationLabel(duration: BindingDuration | null): string | null {
	if (duration === null || bindingDurationError(duration) !== null) return null;
	if (duration.kind === 'preset') {
		return BINDING_DURATION_PRESETS.find((p) => p.value === duration.preset)?.label ?? null;
	}
	if (duration.unit === 'never') return 'No expiry';
	const amount = parseDurationAmount(duration.amount);
	if (amount === null) return null;
	const singular = duration.unit.slice(0, -1);
	return `${amount} ${amount === 1 ? singular : duration.unit}`;
}
