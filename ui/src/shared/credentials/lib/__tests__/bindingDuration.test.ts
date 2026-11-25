import { describe, expect, it } from 'vitest';
import {
	BINDING_DURATION_PRESETS,
	CUSTOM_DURATION_UNITS,
	INITIAL_CUSTOM_DURATION,
	bindingDurationError,
	expiresAtFromDuration,
	expiresAtIso,
	parseDurationAmount,
	type CustomDurationUnit,
} from '@/shared/credentials/lib/bindingDuration';

const NOW = new Date('2026-01-31T12:00:00.000Z');

/** The expiry as an ISO string — fails the test unless it is an actual date. */
function iso(result: Date | 'never' | null): string {
	expect(result).toBeInstanceOf(Date);
	return (result as Date).toISOString();
}

function custom(amount: string, unit: CustomDurationUnit) {
	return { kind: 'custom' as const, amount, unit };
}

describe('parseDurationAmount', () => {
	it('accepts whole numbers from 1 to 999', () => {
		expect(parseDurationAmount('1')).toBe(1);
		expect(parseDurationAmount(' 45 ')).toBe(45);
		expect(parseDurationAmount('999')).toBe(999);
	});

	it('refuses anything else', () => {
		for (const bad of ['', '0', '1000', '-5', '1.5', '1e3', 'ten']) {
			expect(parseDurationAmount(bad)).toBeNull();
		}
	});
});

describe('BINDING_DURATION_PRESETS', () => {
	it('runs from 1 hour to 90 days, with no "No expiry" among them', () => {
		expect(BINDING_DURATION_PRESETS.map((p) => p.label)).toEqual([
			'1 hour',
			'8 hours',
			'1 day',
			'7 days',
			'30 days',
			'90 days',
		]);
	});
});

describe('CUSTOM_DURATION_UNITS', () => {
	it('offers only fixed-length units, plus No expiry — no months', () => {
		expect(CUSTOM_DURATION_UNITS.map((u) => u.value)).toEqual([
			'seconds',
			'minutes',
			'hours',
			'days',
			'never',
		]);
	});

	it('Custom… starts on a day, never on No expiry', () => {
		expect(INITIAL_CUSTOM_DURATION).toEqual(custom('1', 'days'));
	});
});

describe('bindingDurationError', () => {
	it('is silent when nothing is chosen, for a preset, and for No expiry', () => {
		expect(bindingDurationError(null)).toBeNull();
		expect(bindingDurationError({ kind: 'preset', preset: '7d' })).toBeNull();
		expect(bindingDurationError(custom('', 'never'))).toBeNull();
	});

	it('asks for a custom amount when the field is empty, and a valid one otherwise', () => {
		expect(bindingDurationError(custom('', 'hours'))).toBe('Enter how long access lasts.');
		expect(bindingDurationError(custom('0', 'hours'))).toBe(
			'Enter a whole number from 1 to 999.',
		);
	});
});

describe('expiresAtFromDuration', () => {
	it('is null while nothing is chosen: there is no default', () => {
		expect(expiresAtFromDuration(null, NOW)).toBeNull();
	});

	it.each([
		['1h', '2026-01-31T13:00:00.000Z'],
		['8h', '2026-01-31T20:00:00.000Z'],
		['1d', '2026-02-01T12:00:00.000Z'],
		['7d', '2026-02-07T12:00:00.000Z'],
		['30d', '2026-03-02T12:00:00.000Z'],
		['90d', '2026-05-01T12:00:00.000Z'],
	] as const)('preset %s from now', (preset, expected) => {
		expect(iso(expiresAtFromDuration({ kind: 'preset', preset }, NOW))).toBe(expected);
	});

	it.each([
		['30', 'seconds', '2026-01-31T12:00:30.000Z'],
		['10', 'minutes', '2026-01-31T12:10:00.000Z'],
		['2', 'hours', '2026-01-31T14:00:00.000Z'],
		['3', 'days', '2026-02-03T12:00:00.000Z'],
	] as const)('custom %s %s from now', (amount, unit, expected) => {
		expect(iso(expiresAtFromDuration(custom(amount, unit), NOW))).toBe(expected);
	});

	it('the longest custom duration is 999 days, a fixed length', () => {
		expect(iso(expiresAtFromDuration(custom('999', 'days'), NOW))).toBe(
			'2028-10-26T12:00:00.000Z',
		);
	});

	it('is null for an invalid custom amount', () => {
		expect(expiresAtFromDuration(custom('', 'minutes'), NOW)).toBeNull();
	});

	it("is 'never' for No expiry, whatever the amount field holds", () => {
		expect(expiresAtFromDuration(custom('10', 'never'), NOW)).toBe('never');
		expect(expiresAtFromDuration(custom('', 'never'), NOW)).toBe('never');
	});
});

describe('expiresAtIso', () => {
	it('is an ISO string for a duration, and undefined for none, No expiry or a bad amount', () => {
		expect(expiresAtIso({ kind: 'preset', preset: '1h' }, NOW)).toBe(
			'2026-01-31T13:00:00.000Z',
		);
		expect(expiresAtIso(null, NOW)).toBeUndefined();
		expect(expiresAtIso(custom('', 'never'), NOW)).toBeNull();
		expect(expiresAtIso(custom('', 'days'), NOW)).toBeUndefined();
	});
});

describe('an org maximum', () => {
	it('refuses no expiry and anything longer', () => {
		const max = 30 * 86_400;
		expect(bindingDurationError({ kind: 'preset', preset: '30d' }, max)).toBeNull();
		expect(bindingDurationError({ kind: 'preset', preset: '90d' }, max)).toBe(
			'This org allows at most 30 days.',
		);
		expect(bindingDurationError({ kind: 'custom', amount: '1', unit: 'never' }, max)).toBe(
			'This org requires access to end within 30 days.',
		);
		expect(
			bindingDurationError({ kind: 'custom', amount: '720', unit: 'hours' }, max),
		).toBeNull();
		expect(bindingDurationError({ kind: 'preset', preset: '90d' })).toBeNull();
	});
});
