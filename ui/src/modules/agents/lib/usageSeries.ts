/**
 * The agent card's 7-day call series, folded from the usage rollup the card
 * already reads (`useActorUsageDetail`): the actor's own buckets, so the line
 * sums every credential's calls — vendor-wide bindings included, which the
 * per-API row reads can't name — and agrees with the "Calls · 7d" figure.
 */
import type { ActorUsageDetail } from '@/modules/agents/api';

type UsageBucket = ActorUsageDetail['buckets'][number];

const DAY_SECONDS = 86_400;

/**
 * Calls per day over the trailing `days` days ending at `nowMs`, oldest →
 * newest, zero-filled (the rollup is sparse). A bucket is placed by its
 * start; one that starts before the window (the endpoint's minute-ceiled
 * bounds) lands on the first day, never off the chart.
 */
export function dailyCallSeries(
	buckets: readonly Pick<UsageBucket, 'ts' | 'total'>[],
	nowMs: number,
	days = 7,
): number[] {
	const series = new Array<number>(days).fill(0);
	const since = nowMs / 1000 - days * DAY_SECONDS;
	for (const bucket of buckets) {
		const day = Math.floor((bucket.ts - since) / DAY_SECONDS);
		series[Math.min(days - 1, Math.max(0, day))]! += bucket.total;
	}
	return series;
}
