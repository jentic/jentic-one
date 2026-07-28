/**
 * UsageCharts display-bucket tests — the regression fixed here: the volume
 * chart used to render one bar per raw trend segment (12 × 14h for a 7d
 * window), so the weekday axis straddled 8 calendar dates ("7 days" showed
 * more than 7 days) and the 12 min-width bars overflowed narrow screens.
 * The chart now re-buckets trends into mini-style display buckets: one bar
 * per local calendar day for the week view, six range slices otherwise.
 */
import { describe, expect, it } from 'vitest';
import { renderWithProviders } from '@/__tests__/test-utils';
import type { UsageResponse } from '@/modules/monitor/api';
import { usageToEntityRows } from '@/modules/monitor/lib/usage';
import { UsageCharts } from '@/modules/monitor/components/UsageCharts';

/** Day-aligned window bounds like OverviewTab requests: midnight (days-1) days ago → end of today. */
function dayAlignedWindow(days: number): { since: number; until: number } {
	const startOfToday = new Date();
	startOfToday.setHours(0, 0, 0, 0);
	const sinceDate = new Date(startOfToday);
	sinceDate.setDate(sinceDate.getDate() - (days - 1));
	const untilDate = new Date(startOfToday);
	untilDate.setDate(untilDate.getDate() + 1);
	return { since: sinceDate.getTime() / 1000, until: untilDate.getTime() / 1000 };
}

function makeUsage(since: number, until: number, trend: number[]): UsageResponse {
	const total = trend.reduce((sum, v) => sum + v, 0);
	// Aggregate buckets mirror the trend segments exactly, so no "Other"
	// remainder muddies the per-bar assertions.
	const stepSec = (until - since) / trend.length;
	return {
		since,
		until,
		bucket_seconds: stepSec,
		group_by: 'api',
		stats: {
			total,
			success: total,
			failed: 0,
			pending: 0,
			avg_ms: 100,
			p50_ms: 90,
			p95_ms: 200,
			active_now: 0,
		},
		buckets: trend.map((v, i) => ({
			ts: since + i * stepSec,
			total: v,
			success: v,
			failed: 0,
			avg_ms: 100,
		})),
		top: [
			{
				key: 'stripe/stripe-api',
				label: 'stripe/stripe-api',
				total,
				success: total,
				failed: 0,
				avg_ms: 100,
				trend,
			},
		],
	};
}

function renderChart(usage: UsageResponse) {
	const rows = usageToEntityRows(usage);
	return renderWithProviders(<UsageCharts usage={usage} apis={rows} toolkits={[]} agents={[]} />);
}

/** The x-axis M/D sub-labels rendered under the bars (y-ticks are bare numbers). */
function dateSubLabels(container: Element): string[] {
	return [...container.querySelectorAll('svg[role="img"] text')]
		.map((el) => el.textContent ?? '')
		.filter((text) => /^\d{1,2}\/\d{1,2}$/.test(text));
}

describe('UsageCharts display buckets', () => {
	it('renders exactly one bar per calendar day for the 7d window (#7d-overflow)', () => {
		const { since, until } = dayAlignedWindow(7);
		// Backend shape for a 7d window: 28 six-hour segments (4 per day).
		const trend = Array.from({ length: 28 }, (_, i) => (i % 4 === 0 ? 2 : 1));
		const { container, getByText } = renderChart(makeUsage(since, until, trend));

		expect(getByText(/Last 7 days, colored by/)).toBeInTheDocument();

		const dates = dateSubLabels(container);
		expect(dates).toHaveLength(7);
		// First bar is 6 days ago, last bar is today — never an 8th date.
		const first = new Date(since * 1000);
		const today = new Date();
		expect(dates[0]).toBe(`${first.getMonth() + 1}/${first.getDate()}`);
		expect(dates[6]).toBe(`${today.getMonth() + 1}/${today.getDate()}`);
	});

	it('caps wider windows at six range bars so the axis fits narrow screens', () => {
		const { since, until } = dayAlignedWindow(30);
		const trend = Array.from({ length: 30 }, () => 1);
		const { container, getByText } = renderChart(makeUsage(since, until, trend));

		expect(getByText(/Last 30 days, colored by/)).toBeInTheDocument();
		// Six bars, each labelled with a start date and an –end date sub-label.
		expect(dateSubLabels(container).length).toBeLessThanOrEqual(12);
		const rangeSubs = [...container.querySelectorAll('svg[role="img"] text')]
			.map((el) => el.textContent ?? '')
			.filter((text) => /^–\d{1,2}\/\d{1,2}$/.test(text));
		expect(rangeSubs).toHaveLength(6);
		// Range sub-labels are inclusive: the last slice ends at midnight
		// tomorrow (exclusive), so it must be labelled with today's date.
		const today = new Date();
		expect(rangeSubs[5]).toBe(`–${today.getMonth() + 1}/${today.getDate()}`);
	});

	it('re-buckets aggregate buckets and trends identically (no phantom "Other")', () => {
		const { since, until } = dayAlignedWindow(7);
		const trend = Array.from({ length: 28 }, (_, i) => i + 1);
		const usage = makeUsage(since, until, trend);
		const { container, queryByText } = renderChart(usage);

		const svg = container.querySelector('svg[role="img"]');
		expect(svg).not.toBeNull();
		expect(svg!.getAttribute('aria-label')).toContain('stacked by API');
		// The fixture's aggregate buckets equal its trend segments, so the two
		// series must land in the same day bars — any drift would surface as a
		// muted "Other" remainder chip in the legend.
		expect(queryByText('Other')).not.toBeInTheDocument();
	});
});
