import { describe, expect, it } from 'vitest';
import { afterExpiryFrom, expiryWarnable, expiryWarning } from '@/shared/credentials/lib/accessEnd';

const DAY = 86_400;
const NOW = Date.parse('2026-11-24T12:00:00Z');

describe('expiryWarnable / expiryWarning', () => {
	it('offers a warning only when every limit is 4 days or more', () => {
		expect(expiryWarnable(new Date(NOW + 7 * DAY * 1000), null, NOW)).toBe(true);
		expect(expiryWarnable(new Date(NOW + 4 * DAY * 1000), null, NOW)).toBe(true);
		expect(expiryWarnable(new Date(NOW + 2 * DAY * 1000), null, NOW)).toBe(false);
		expect(expiryWarnable('never', 14 * DAY, NOW)).toBe(true);
		expect(expiryWarnable('never', 3 * DAY, NOW)).toBe(false);
		expect(expiryWarnable('never', null, NOW)).toBe(false);
		expect(expiryWarnable(null, null, NOW)).toBe(false);
		// Either limit under 4 days rules it out, even beside a longer one.
		expect(expiryWarnable(new Date(NOW + 30 * DAY * 1000), 3 * DAY, NOW)).toBe(false);
		expect(expiryWarnable(new Date(NOW + 2 * DAY * 1000), 14 * DAY, NOW)).toBe(false);
		expect(expiryWarnable(new Date(NOW + 30 * DAY * 1000), 14 * DAY, NOW)).toBe(true);
	});

	it('sends a ticked warning only when there is something to warn about', () => {
		expect(expiryWarning(true, 'never', 14 * DAY)).toBe(true);
		expect(expiryWarning(false, 'never', 14 * DAY)).toBe(false);
		expect(expiryWarning(true, 'never', null)).toBe(false);
	});
});

describe('afterExpiryFrom', () => {
	it('reads refuse, and anything else as hold', () => {
		expect(afterExpiryFrom('refuse')).toBe('refuse');
		expect(afterExpiryFrom('hold')).toBe('hold');
		expect(afterExpiryFrom(undefined)).toBe('hold');
	});
});
