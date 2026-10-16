import { describe, it, expect } from 'vitest';
import { USAGE_TOP_LIMIT, USAGE_WINDOW_STEP_MS, usageWindowBounds } from '@/modules/monitor/api';

const STEP_SEC = USAGE_WINDOW_STEP_MS / 1000;

describe('usageWindowBounds', () => {
	it('rolls the 24h window to the NEXT step boundary', () => {
		const nowSec = 1_700_000_100; // already floored by the caller
		const { since, until } = usageWindowBounds(nowSec, 1, STEP_SEC);
		expect(until).toBe(nowSec + STEP_SEC);
		expect(until - since).toBe(86_400);
	});

	it('aligns multi-day windows to local calendar days, through the end of today', () => {
		const now = new Date(2026, 2, 10, 15, 30, 0);
		const { since, until } = usageWindowBounds(now.getTime() / 1000, 7, STEP_SEC);
		expect(new Date(since * 1000)).toEqual(new Date(2026, 2, 4, 0, 0, 0));
		expect(new Date(until * 1000)).toEqual(new Date(2026, 2, 11, 0, 0, 0));
	});

	it('defaults to the canonical step so callers share cache keys', () => {
		const nowSec = 1_700_000_100;
		expect(usageWindowBounds(nowSec, 1)).toEqual(usageWindowBounds(nowSec, 1, STEP_SEC));
	});

	it('exposes the shared chart sizing constants', () => {
		expect(USAGE_TOP_LIMIT).toBe(12);
		expect(USAGE_WINDOW_STEP_MS).toBe(300_000);
	});
});
