/**
 * Actor directory — service tier (TanStack Query).
 *
 * Hydrates the unified actor lookup (`GET /actors`, PR #483) once and caches it
 * aggressively as reference data, exposing a lookup `Map` plus a `resolve(id)`
 * convenience so any surface can turn a raw `actor_id` (a KSUID like
 * `agnt_6a3d3c62…`) into a friendly name.
 *
 * The directory is small relative to executions and rarely changes, so this is
 * deliberately a long-`staleTime` query under a single stable key — every
 * consumer (monitor, dashboard, agents, toolkits, access-requests) shares one
 * cache slice and one network fetch.
 *
 * Unauthenticated-safe: the query is gated on holding a Bearer token, so it
 * never fires (or crashes) before login. The token store is the same source of
 * truth `AuthContext` subscribes to.
 */
import { useMemo, useSyncExternalStore } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getToken, subscribeToken, type ActorSummaryResponse } from '@/shared/api';
import { fetchActorDirectory } from '@/shared/lib/actorDirectory';

/** Stable key so every consumer shares one cached directory slice. */
export const actorDirectoryKey = ['actor-directory'] as const;

/** Reference data — refetch at most every 5 minutes. */
const ACTOR_DIRECTORY_STALE_TIME = 5 * 60_000;

export interface ActorDirectory {
	/** All actors keyed by their opaque `id`. Empty while loading/unauthenticated. */
	byId: Map<string, ActorSummaryResponse>;
	/** Friendly name for an id, or `undefined` when unknown / not yet loaded. */
	resolve: (id: string | null | undefined) => string | undefined;
	isLoading: boolean;
	isError: boolean;
}

/** Subscribe to the token store so the hook re-gates on login/logout. */
function useHasToken(): boolean {
	return useSyncExternalStore(
		subscribeToken,
		() => getToken() !== null,
		() => false,
	);
}

export function useActorDirectory(): ActorDirectory {
	const hasToken = useHasToken();

	const { data, isLoading, isError } = useQuery({
		queryKey: actorDirectoryKey,
		queryFn: fetchActorDirectory,
		enabled: hasToken,
		staleTime: ACTOR_DIRECTORY_STALE_TIME,
		gcTime: ACTOR_DIRECTORY_STALE_TIME,
		refetchOnWindowFocus: false,
	});

	return useMemo<ActorDirectory>(() => {
		const byId = new Map<string, ActorSummaryResponse>();
		for (const actor of data ?? []) byId.set(actor.id, actor);
		return {
			byId,
			resolve: (id) => (id != null ? byId.get(id)?.name : undefined),
			// Gated-off (unauthenticated) is not "loading" — there is nothing to wait for.
			isLoading: hasToken && isLoading,
			isError,
		};
	}, [data, hasToken, isLoading, isError]);
}
