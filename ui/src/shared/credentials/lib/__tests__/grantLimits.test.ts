import { describe, expect, it } from 'vitest';
import {
	NO_GRANT_LIMITS,
	limitLabel,
	preselectedSeconds,
} from '@/shared/credentials/lib/grantLimits';
import { grantDefaults } from '@/shared/credentials/lib/grantDefaults';

const DAY = 86_400;

describe('grant limits', () => {
	it('preselect nothing in open source', () => {
		expect(grantDefaults(NO_GRANT_LIMITS)).toEqual({ duration: null, idle: null, uses: null });
	});

	it("preselect the org's default, else its maximum", () => {
		const limits = {
			...NO_GRANT_LIMITS,
			expires: { defaultSeconds: null, maxSeconds: 30 * DAY },
			idle: { defaultSeconds: 14 * DAY, maxSeconds: null },
			singleUseByDefault: true,
			loaded: true,
		};
		expect(preselectedSeconds(limits.expires)).toBe(30 * DAY);
		expect(grantDefaults(limits)).toEqual({
			duration: { kind: 'preset', preset: '30d' },
			idle: { kind: 'preset', preset: '14d' },
			uses: 1,
		});
	});

	it('name a limit in words', () => {
		expect(limitLabel(DAY)).toBe('1 day');
		expect(limitLabel(12 * 3_600)).toBe('12 hours');
		expect(limitLabel(90)).toBe('90 seconds');
	});
});
