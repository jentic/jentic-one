/**
 * The org's defaults and maximums for new grants (credential grant
 * expiry, step 8), as the console uses them: preselect the default, disable
 * choices above a maximum, and require a limit a maximum makes mandatory. The
 * server applies the same limits on every path, so this only keeps the console
 * from offering what it would refuse.
 */
export interface DurationLimit {
	/** Seconds; null when the org sets none. */
	defaultSeconds: number | null;
	maxSeconds: number | null;
}

export interface GrantLimits {
	/** A grant's end date, as a lifetime. */
	expires: DurationLimit;
	/** A grant's idle limit. */
	idle: DurationLimit;
	/** Whether a new grant starts as single use. */
	singleUseByDefault: boolean;
	/** Whether the org's limits have been read (false while loading or after a failure). */
	loaded: boolean;
}

const NO_LIMIT: DurationLimit = { defaultSeconds: null, maxSeconds: null };

/** No defaults and no maximums: the open-source default, and the value while loading. */
export const NO_GRANT_LIMITS: GrantLimits = {
	expires: NO_LIMIT,
	idle: NO_LIMIT,
	singleUseByDefault: false,
	loaded: false,
};

/** The default to preselect: the org's default, else its maximum (every grant must end). */
export function preselectedSeconds(limit: DurationLimit): number | null {
	return limit.defaultSeconds ?? limit.maxSeconds;
}

/** "30 days", "12 hours", "45 minutes" — a limit in words. */
export function limitLabel(seconds: number): string {
	for (const [unit, size] of [
		['day', 86_400],
		['hour', 3_600],
		['minute', 60],
	] as const) {
		if (seconds % size === 0) {
			const n = seconds / size;
			return `${n} ${unit}${n === 1 ? '' : 's'}`;
		}
	}
	return `${seconds} second${seconds === 1 ? '' : 's'}`;
}
