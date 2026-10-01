/**
 * Who can reach each workspace API — joined from two real reads:
 *
 *   - `GET /credentials` (drained, {@link useAllCredentials}): every credential
 *     carries the API scope it targets (`api`). A scope's `name`/`version` may
 *     be wildcards (`""`) — the create flow saves credentials unpinned
 *     (`version: ""` = any version) — so "credentials for this API" is every
 *     ACTIVE credential whose scope COVERS it ({@link apiScopeCovers}, the UI
 *     mirror of the broker's `credential_covers`), not an exact-triple group.
 *     An inactive credential can't serve a call, so it never counts. This is
 *     the one credential ↔ API rule: the catalog's "Credential ready" and the
 *     hub / panel / tile "No credential" all read it.
 *   - `GET /credentials/{id}/agents` (first page, per credential): the agents
 *     directly bound to a credential — theme 5's agent ↔ credential binding,
 *     the same read the credential sheet's "Bound agents" section uses (and the
 *     same `credentialKeys.agents(id)` cache slice, so the two never disagree).
 *     These fan out one request per credential, so they are read LAZILY
 *     ({@link useAgentAccess}) — only for the credentials of the rows a surface
 *     actually shows — and held fresh for minutes, not refetched on focus.
 *     A page that reports `has_more` marks the answer truncated, so its count
 *     reads as a floor ("50+"), never as the whole list. A surface that must
 *     know EVERY bound agent of one credential drains it instead
 *     (`useAllCredentialAgents`).
 *
 * Shared (not in one feature module) because two modules read it: the Library
 * catalog's docked "Your workspace" panel (discover) and the API hub
 * (workspace). Neither may import the other, so the join lives here once.
 *
 * Imported by its own path (`@/shared/credentials/api/apiAccess`), not through
 * the `api` barrel: it builds on the barrel's hooks, so re-exporting it there
 * would close an import cycle.
 */
import { useCallback, useMemo } from 'react';
import { useQueries } from '@tanstack/react-query';
import type { CredentialAgentResponse, CredentialRedactedResponse } from '@/shared/api';
import { apiScopeCovers } from '@/shared/credentials/lib/apiIdentity';
import { listCredentialAgents } from './client';
import { credentialKeys, useAllCredentials } from './index';

/** The `vendor/name/version` key for a concrete API reference. */
export function apiAccessKey(ref: { vendor: string; name: string; version: string }): string {
	return `${ref.vendor}/${ref.name}/${ref.version}`;
}

type ApiRef = { vendor: string; name: string; version: string };
type Credential = CredentialRedactedResponse;

export interface ApiAccessEntry {
	/** Active credentials whose `api` scope covers this API (pinned or wildcard). */
	credentials: Credential[];
}

export interface ApiAccessIndex {
	/**
	 * The active credentials that can reach `ref`; `undefined` when none covers
	 * it. Stable per index build for a given `ref`.
	 */
	entryFor: (ref: ApiRef) => ApiAccessEntry | undefined;
	/**
	 * Every credential page loaded — only then may a caller assert "this API
	 * has NO credential" (an absent key before that just means "not loaded yet").
	 */
	credentialsComplete: boolean;
	isPending: boolean;
	/** The credential list failed to load (it will never complete on its own). */
	error: Error | null;
	/** Retry a failed credential list read. */
	retry: () => void;
}

export function useApiAccessIndex(opts: { enabled?: boolean } = {}): ApiAccessIndex {
	const credentials = useAllCredentials({ enabled: opts.enabled ?? true });
	const creds = credentials.items;

	// Per-API entries, computed on first ask and memoised for this build of the
	// index (a fresh cache whenever the credential list changes), so every
	// surface asking about one API gets the same object back.
	// eslint-disable-next-line react-hooks/exhaustive-deps -- a fresh cache per credential list
	const cache = useMemo(() => new Map<string, ApiAccessEntry | undefined>(), [creds]);
	const entryFor = useCallback(
		(ref: ApiRef): ApiAccessEntry | undefined => {
			const key = apiAccessKey(ref);
			if (cache.has(key)) return cache.get(key);
			const covering = creds.filter((c) => c.active && apiScopeCovers(c.api, ref));
			const entry = covering.length > 0 ? { credentials: covering } : undefined;
			cache.set(key, entry);
			return entry;
		},
		[cache, creds],
	);

	return {
		entryFor,
		credentialsComplete: credentials.complete,
		isPending: credentials.isPending,
		error: credentials.error,
		retry: credentials.retry,
	};
}

/** The agents bound to one set of credentials, as far as the reads answered. */
export interface AgentAccess {
	/** Distinct agents bound to any of the credentials (first page of each). */
	agents: CredentialAgentResponse[];
	/** Every credential's agents read has settled — answered or failed. */
	agentsSettled: boolean;
	/** At least one of those reads failed, so `agents` may be missing some. */
	agentsError: boolean;
	/**
	 * `agents` may be missing some: a credential fell past the read cap, or its
	 * first page reported more agents (`has_more`). What was read is a floor.
	 */
	agentsTruncated: boolean;
}

/** The `agents` list is the whole answer: settled, nothing failed, nothing capped. */
export function agentsExhaustive(access: AgentAccess): boolean {
	return access.agentsSettled && !access.agentsError && !access.agentsTruncated;
}

/** Cap on the per-credential agent reads one surface issues, so a huge org can't fan out unbounded. */
const MAX_AGENT_READS = 100;
/** Bindings change rarely and every bind/unbind invalidates the slice, so reads stay fresh for minutes. */
const FAN_OUT_STALE_MS = 5 * 60_000;

/**
 * Read the bound agents of exactly the credentials in `credentialSets` — one
 * set per row a surface shows (a hub, the panel's visible rows, the tiles on
 * screen). Nothing else is fetched. Returns `accessFor(credentials)` for any
 * of those sets; `null` in means the credential list itself is still loading.
 */
export function useAgentAccess(
	credentialSets: ReadonlyArray<readonly Credential[] | null | undefined>,
): (credentials: readonly Credential[] | null | undefined) => AgentAccess | null {
	const ids: string[] = [];
	const seen = new Set<string>();
	for (const set of credentialSets) {
		for (const c of set ?? []) {
			if (seen.has(c.credential_id)) continue;
			seen.add(c.credential_id);
			ids.push(c.credential_id);
		}
	}
	const idsKey = ids.join('\n');
	// eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on the id list's content, not its identity
	const readIds = useMemo(() => ids.slice(0, MAX_AGENT_READS), [idsKey]);

	const queries = useQueries({
		queries: readIds.map((id) => ({
			queryKey: credentialKeys.agents(id),
			queryFn: () => listCredentialAgents(id),
			staleTime: FAN_OUT_STALE_MS,
			refetchOnWindowFocus: false,
		})),
	});
	// `useQueries` returns a fresh array each render; key the memo on each
	// read's state so `accessFor` is stable between unrelated renders.
	const stamp = queries.map((q) => `${q.status}:${q.dataUpdatedAt}`).join(',');
	const byId = useMemo(
		() =>
			new Map(
				readIds.map((id, i) => [
					id,
					{ status: queries[i]?.status ?? 'pending', data: queries[i]?.data },
				]),
			),
		// eslint-disable-next-line react-hooks/exhaustive-deps -- `stamp` tracks `queries`
		[readIds, stamp],
	);

	return useCallback(
		(credentials: readonly Credential[] | null | undefined): AgentAccess | null => {
			if (credentials == null) return null;
			const access: AgentAccess = {
				agents: [],
				agentsSettled: true,
				agentsError: false,
				agentsTruncated: false,
			};
			for (const cred of credentials) {
				const read = byId.get(cred.credential_id);
				if (!read) {
					// Past the cap: never read, so the agent list can't claim to be whole.
					access.agentsTruncated = true;
					continue;
				}
				if (read.status === 'pending') access.agentsSettled = false;
				if (read.status === 'error') access.agentsError = true;
				// First page only: more bound agents than one page ⇒ a floor.
				if (read.data?.has_more) access.agentsTruncated = true;
				for (const agent of read.data?.data ?? []) {
					if (!access.agents.some((a) => a.agent_id === agent.agent_id)) {
						access.agents.push(agent);
					}
				}
			}
			return access;
		},
		[byId],
	);
}
