/**
 * Actor directory — service tier (TanStack Query).
 *
 * Turns a raw `actor_id` (a KSUID like `agnt_6a3d3c62…`) into a friendly name,
 * exposing a lookup `Map` plus a `resolve(id)` convenience. Two sources, picked
 * by what the caller may read:
 *
 *   - Callers with `users:read` (or `org:admin`) hydrate the whole directory
 *     (`GET /actors`) once and cache it aggressively as reference data, under a
 *     single stable key shared by every consumer (monitor, rail, agents).
 *   - Everyone else — and anyone whose full listing fails — resolves just the
 *     ids passed to the hook through `GET /actors/lookup`. Each id is its own
 *     cache entry under the same root, and ids requested together are batched
 *     into one call (`loadActor`).
 *
 * Unauthenticated-safe: both queries are gated on holding a Bearer token, so
 * they never fire (or crash) before login. The token store is the same source
 * of truth `AuthContext` subscribes to.
 */
import { useMemo, useSyncExternalStore } from 'react';
import { useQueries, useQuery } from '@tanstack/react-query';
import { getToken, subscribeToken } from '@/shared/api';
import { sharedQueryKeys } from '@/shared/api/queryKeys';
import { useOptionalAuth } from '@/shared/auth/AuthContext';
import { ORG_ADMIN } from '@/shared/auth/usePermission';
import {
	fetchActorDirectory,
	loadActor,
	type ActorDirectoryEntry,
} from '@/shared/lib/actorDirectory';

/**
 * Stable key so every consumer shares one cached directory slice. Defined in
 * the shared key registry because the live event stream (shared/lib) must
 * invalidate it when an agent registers — see `actorDirectoryRoot`. The per-id
 * lookups live under the same root, so that invalidation refreshes them too.
 */
export const actorDirectoryKey = sharedQueryKeys.actorDirectoryRoot;

const actorLookupKey = (id: string) => [...actorDirectoryKey, 'lookup', id] as const;

/** Reference data — refetch at most every 5 minutes. */
const ACTOR_DIRECTORY_STALE_TIME = 5 * 60_000;

/** The permission `GET /actors` requires. */
const USERS_READ = 'users:read';

export interface ActorDirectory {
	/** Known actors keyed by their opaque `id`. Empty while loading/unauthenticated. */
	byId: Map<string, ActorDirectoryEntry>;
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

/**
 * Whether the caller may list the whole directory, or `undefined` while the
 * signed-in user is still loading. Outside an `AuthProvider` (shell chrome in
 * isolation, tests) the listing is tried; a failure falls back to the by-id
 * lookup.
 */
export function useCanListActors(): boolean | undefined {
	const auth = useOptionalAuth();
	if (auth === null) return true;
	const { status } = auth;
	const permissions = auth.user?.permissions;
	if (permissions == null) return status === 'loading' ? undefined : true;
	return permissions.includes(USERS_READ) || permissions.includes(ORG_ADMIN);
}

/**
 * @param ids The actor ids this surface needs names for. Only consulted when
 *   the full directory is unavailable to the caller; with it, every id
 *   resolves from the one cached listing.
 */
export function useActorDirectory(
	ids: readonly (string | null | undefined)[] = [],
): ActorDirectory {
	const hasToken = useHasToken();
	const canList = useCanListActors();

	const full = useQuery({
		queryKey: actorDirectoryKey,
		queryFn: fetchActorDirectory,
		enabled: hasToken && canList === true,
		staleTime: ACTOR_DIRECTORY_STALE_TIME,
		gcTime: ACTOR_DIRECTORY_STALE_TIME,
		refetchOnWindowFocus: false,
	});

	const lookupMode = hasToken && (canList === false || full.isError);
	const idsKey = ids.filter((id): id is string => !!id).join('\u0000');
	const lookupIds = useMemo(
		() => (lookupMode && idsKey ? [...new Set(idsKey.split('\u0000'))] : []),
		[lookupMode, idsKey],
	);

	// `combine` output is structurally shared, so `lookup` keeps its identity
	// until an entry or a status actually changes.
	const lookup = useQueries({
		queries: lookupIds.map((id) => ({
			queryKey: actorLookupKey(id),
			queryFn: () => loadActor(id),
			staleTime: ACTOR_DIRECTORY_STALE_TIME,
			gcTime: ACTOR_DIRECTORY_STALE_TIME,
			refetchOnWindowFocus: false,
		})),
		combine: (results) => ({
			actors: results.map((r) => r.data).filter((a): a is ActorDirectoryEntry => a != null),
			isLoading: results.some((r) => r.isLoading),
			isError: results.some((r) => r.isError),
		}),
	});

	return useMemo<ActorDirectory>(() => {
		const byId = new Map<string, ActorDirectoryEntry>();
		for (const actor of full.data ?? []) byId.set(actor.id, actor);
		for (const actor of lookup.actors) byId.set(actor.id, actor);
		return {
			byId,
			resolve: (id) => (id != null ? byId.get(id)?.name : undefined),
			// Gated-off (unauthenticated) is not "loading" — there is nothing to wait for.
			isLoading:
				hasToken &&
				(canList === undefined || (canList && full.isLoading) || lookup.isLoading),
			// A failed listing the lookup stands in for is not an error.
			isError: (full.isError && lookupIds.length === 0) || lookup.isError,
		};
	}, [full.data, full.isLoading, full.isError, lookup, lookupIds.length, hasToken, canList]);
}
