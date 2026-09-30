/**
 * The ONE set of health + latency cutoffs for usage views.
 *
 * Monitor and the Dashboard used to hard-code their own numbers (97/90 vs
 * 99/90, latency tiers duplicated in two components), so the same API could be
 * "healthy" on one page and "degraded" on the other. Every usage view reads
 * these instead.
 */

export type HealthTier = 'healthy' | 'degraded' | 'failing';
export type LatencyTier = 'fast' | 'normal' | 'slow';

/** Success rate (0–100) at or above which a window/row is healthy. */
export const HEALTHY_SUCCESS_RATE = 97;
/** Success rate (0–100) at or above which a window/row is degraded (below = failing). */
export const DEGRADED_SUCCESS_RATE = 90;

/** Latency (ms) at or below which a response is fast. */
export const FAST_LATENCY_MS = 300;
/** Latency (ms) at or below which a response is normal (above = slow). */
export const NORMAL_LATENCY_MS = 800;

export function healthTier(successRate: number): HealthTier {
	if (successRate >= HEALTHY_SUCCESS_RATE) return 'healthy';
	if (successRate >= DEGRADED_SUCCESS_RATE) return 'degraded';
	return 'failing';
}

export function latencyTier(ms: number): LatencyTier {
	if (ms <= FAST_LATENCY_MS) return 'fast';
	if (ms <= NORMAL_LATENCY_MS) return 'normal';
	return 'slow';
}

export const HEALTH_LABEL: Record<HealthTier, string> = {
	healthy: 'Healthy',
	degraded: 'Degraded',
	failing: 'Failing',
};

/** Tailwind background class for a health dot. */
export const HEALTH_DOT_CLASS: Record<HealthTier, string> = {
	healthy: 'bg-accent-green',
	degraded: 'bg-accent-amber',
	failing: 'bg-danger',
};

/** Tailwind text class for a health value. */
export const HEALTH_TEXT_CLASS: Record<HealthTier, string> = {
	healthy: 'text-accent-green',
	degraded: 'text-accent-amber',
	failing: 'text-danger',
};

/** Tailwind text class for a latency value. */
export const LATENCY_TEXT_CLASS: Record<LatencyTier, string> = {
	fast: 'text-accent-green',
	normal: 'text-foreground',
	slow: 'text-danger',
};
