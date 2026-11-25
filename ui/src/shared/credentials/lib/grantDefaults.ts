/**
 * What a new grant's controls start on: the org's defaults (or its maximum,
 * when it sets only that), and nothing at all in open source, where the
 * operator must choose (D8).
 */
import { useEffect, useRef } from 'react';
import {
	durationFromSeconds,
	type BindingDuration,
} from '@/shared/credentials/lib/bindingDuration';
import { idleLimitFromSeconds, type IdleLimit } from '@/shared/credentials/lib/idleLimit';
import { preselectedSeconds, type GrantLimits } from '@/shared/credentials/lib/grantLimits';

export interface GrantDefaults {
	duration: BindingDuration | null;
	idle: IdleLimit | null;
	/** 1 when single use is on by default, else null. */
	uses: number | null;
}

export function grantDefaults(limits: GrantLimits): GrantDefaults {
	const expires = preselectedSeconds(limits.expires);
	const idle = preselectedSeconds(limits.idle);
	return {
		duration: expires !== null ? durationFromSeconds(expires) : null,
		idle: idle !== null ? idleLimitFromSeconds(idle) : null,
		uses: limits.singleUseByDefault ? 1 : null,
	};
}

/**
 * Call `apply` once with the org's defaults when its limits first load (the
 * controls render before they do). `apply` should leave a choice the operator
 * already made alone.
 */
export function usePreselectGrantDefaults(
	limits: GrantLimits,
	apply: (defaults: GrantDefaults) => void,
): void {
	const applied = useRef(false);
	const latest = useRef(apply);
	latest.current = apply;
	useEffect(() => {
		if (!limits.loaded || applied.current) return;
		applied.current = true;
		latest.current(grantDefaults(limits));
	}, [limits]);
}
