/**
 * Per-API health signals, joined once from already-shared reads so every
 * surface that shows them (the Library's "Your workspace" digest and docked
 * panel) derives them identically:
 *
 *   | signal                        | source                                                 |
 *   |-------------------------------|--------------------------------------------------------|
 *   | credentials covering the API  | `GET /credentials` (drained) via {@link useApiAccessIndex} |
 *   | 7-day calls / failures / line | `GET /monitoring/usage?group_by=api` (org:admin only)  |
 *   | agents with access            | `GET /credentials/{id}/agents` — {@link useAgentFigures}, |
 *   |                               | read only for the rows a surface actually shows        |
 *
 * The underlying hooks are TanStack queries with shared keys, so mounting this
 * in several places (or once per page) costs no extra requests.
 *
 * Imported by its own path, not through the `api` barrel (it builds on
 * `apiAccess`, which is barrel-external for the same cycle reason).
 */
import { useCallback, useMemo } from 'react';
import { agentsExhaustive, useAgentAccess, useApiAccessIndex } from './apiAccess';
import type { CredentialRedactedResponse } from '@/shared/api';
import { apiUsageKeyFor, useApiUsageWeek, type ApiUsageRow } from '@/shared/hooks';

type ApiRef = { vendor: string; name: string; version: string };

const NONE: CredentialRedactedResponse[] = [];

export interface ApiHealth {
	/** Active credentials covering this API; null until every credential page loaded. */
	credentials: CredentialRedactedResponse[] | null;
	/** `credentials.length`; null until every credential page loaded. */
	credentialCount: number | null;
	/**
	 * 7-day usage row (null when usage is unreadable or the API has no row —
	 * combine with `usageExhaustive` via {@link callsInWeek} before reading 0).
	 */
	usage: ApiUsageRow | null;
}

export interface ApiHealthIndex {
	healthFor: (ref: ApiRef) => ApiHealth;
	/** 7-day usage is readable for this user (org:admin) and loaded. */
	usageAvailable: boolean;
	/** Every API with traffic is in the usage rows (a missing row ⇒ 0 calls). */
	usageExhaustive: boolean;
	/** Usage is loading for a user who can read it (reserve its space). */
	usageLoading: boolean;
	/** Every credential page loaded — "no credential" claims are safe. */
	credentialsComplete: boolean;
	/** The credential list failed to load. */
	credentialsError: boolean;
}

/** The agents-with-access figure for one API. */
export interface AgentFigure {
	/**
	 * Distinct agents bound to the API's credentials; null while loading, when
	 * a read failed, or when a truncated read found none to count from.
	 */
	agentCount: number | null;
	/**
	 * `agentCount` is a floor, not the total: a credential has more bound agents
	 * than one page, or fell past the read cap. Render it as "N+".
	 */
	agentsAtLeast: boolean;
	/** The count is still on its way (as opposed to unknowable: failed/capped). */
	agentsLoading: boolean;
}

/**
 * Agents-with-access figures for the rows a surface shows. Pass every shown
 * row's `credentials` (from `healthFor`); only those credentials' agent reads
 * are issued. The returned function maps one row's `credentials` to its figure.
 */
export function useAgentFigures(
	credentialSets: ReadonlyArray<CredentialRedactedResponse[] | null>,
	credentialsError: boolean,
): (credentials: CredentialRedactedResponse[] | null) => AgentFigure {
	const accessFor = useAgentAccess(credentialSets);
	return useCallback(
		(credentials) => {
			if (credentials == null) {
				return { agentCount: null, agentsAtLeast: false, agentsLoading: !credentialsError };
			}
			const access = accessFor(credentials);
			if (!access) return { agentCount: null, agentsAtLeast: false, agentsLoading: true };
			const agentsLoading = !access.agentsSettled;
			if (agentsExhaustive(access)) {
				return { agentCount: access.agents.length, agentsAtLeast: false, agentsLoading };
			}
			// Truncated but otherwise whole: what was read is a floor ("50+").
			const floor = access.agentsSettled && !access.agentsError && access.agents.length > 0;
			return {
				agentCount: floor ? access.agents.length : null,
				agentsAtLeast: floor,
				agentsLoading,
			};
		},
		[accessFor, credentialsError],
	);
}

export function useApiHealthIndex(): ApiHealthIndex {
	const access = useApiAccessIndex();
	const { entryFor } = access;
	const credentialsError = access.error != null;
	const usage = useApiUsageWeek();

	const healthFor = useCallback(
		(ref: ApiRef): ApiHealth => {
			const entry = entryFor(ref);
			const credentials = access.credentialsComplete ? (entry?.credentials ?? NONE) : null;
			return {
				credentials,
				credentialCount: credentials?.length ?? null,
				usage: usage.available ? (usage.byApi.get(apiUsageKeyFor(ref)) ?? null) : null,
			};
		},
		[entryFor, access.credentialsComplete, usage.available, usage.byApi],
	);

	return useMemo(
		() => ({
			healthFor,
			usageAvailable: usage.available,
			usageExhaustive: usage.available && usage.exhaustive,
			usageLoading: usage.isLoading,
			credentialsComplete: access.credentialsComplete,
			credentialsError,
		}),
		[
			healthFor,
			usage.available,
			usage.exhaustive,
			usage.isLoading,
			access.credentialsComplete,
			credentialsError,
		],
	);
}

/**
 * "Agents can't call it": the API needs auth and — with every credential page
 * loaded (`credentialCount` non-null) — no active credential covers it.
 *
 * The workspace tiles and the Library panel pass the DECLARED schemes as
 * `needsAuth` (`GET /apis` carries only those); the API hub decides from the
 * live spec's required security instead (`useApiAuthRequirement`).
 */
export function isCredentialMissing(needsAuth: boolean, credentialCount: number | null): boolean {
	return needsAuth && credentialCount === 0;
}

/**
 * 7-day call count to display, or null when unknown: a missing row reads as 0
 * only when the usage list is exhaustive (not capped at the top-N).
 */
export function callsInWeek(usage: ApiUsageRow | null, usageExhaustive: boolean): number | null {
	return usage?.total ?? (usageExhaustive ? 0 : null);
}
