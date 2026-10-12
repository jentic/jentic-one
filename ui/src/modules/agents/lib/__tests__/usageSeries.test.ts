import { describe, expect, it } from 'vitest';
import { dailyCallSeries } from '@/modules/agents/lib/usageSeries';

const NOW = Date.UTC(2026, 9, 7, 12, 0, 0);
const nowSec = NOW / 1000;
const DAY = 86_400;

describe('dailyCallSeries', () => {
	it('zero-fills seven days for an empty rollup', () => {
		expect(dailyCallSeries([], NOW)).toEqual([0, 0, 0, 0, 0, 0, 0]);
	});

	it('sums the sparse 6h buckets into their day, oldest first', () => {
		const series = dailyCallSeries(
			[
				{ ts: nowSec - 6.5 * DAY, total: 2 },
				{ ts: nowSec - 6.4 * DAY, total: 3 },
				{ ts: nowSec - 0.25 * DAY, total: 7 },
			],
			NOW,
		);
		expect(series).toEqual([5, 0, 0, 0, 0, 0, 7]);
	});

	it('keeps a bucket starting just outside the window on the chart', () => {
		const series = dailyCallSeries(
			[
				{ ts: nowSec - 7 * DAY - 60, total: 1 },
				{ ts: nowSec + 30, total: 4 },
			],
			NOW,
		);
		expect(series[0]).toBe(1);
		expect(series[6]).toBe(4);
		expect(series.reduce((a, b) => a + b, 0)).toBe(5);
	});
});
