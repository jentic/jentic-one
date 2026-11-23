// What an action rule's ids mean beyond the API's current operation list: the
// spec `operationId` each current operation carries (which survives a
// re-import), what an id of an earlier revision was, and which `operationId`s
// another API the credential covers declares too. Query keys live under
// `['credentials', 'action-identity', …]`.
import { useMemo } from 'react';
import { useQueries, useQuery } from '@tanstack/react-query';
import {
	actionAt,
	registryOperationId,
	specActions,
	storedOperationIds,
} from '@/shared/credentials/lib/specOperationIds';
import { getApiRevisionSpec, getApiSpec, listApiRevisions, listApis } from './apis';

/** The API's coordinates (all three set, or nothing to read). */
export interface ActionApiReference {
	vendor: string;
	name: string | null;
	version: string | null;
}

/** An action of an earlier revision, by the id a rule may still hold for it. */
export interface PastAction {
	method: string;
	path: string;
	summary: string | null;
}

const KEY = ['credentials', 'action-identity'] as const;
/** Specs of a stored revision never change; a list of revisions rarely does. */
const STALE_MS = 10 * 60 * 1000;
/** Earlier revisions read to name stale ids — the newest ones. */
const MAX_PAST_REVISIONS = 10;
/** Other covered APIs read to tell a unique `operationId`; past this, unknown. */
const MAX_COVERED_APIS = 12;

const ready = (
	api: ActionApiReference | null | undefined,
): api is {
	vendor: string;
	name: string;
	version: string;
} => !!api?.vendor && !!api.name && !!api.version;

/** One revision's stored `operationId`s by action, or whether they are known yet. */
export interface RevisionOperationIds {
	/** Read (or nothing to read); false while loading, or when the read failed. */
	known: boolean;
	ids: ReadonlyMap<string, string>;
}

const NO_IDS: ReadonlyMap<string, string> = new Map();

const revisionIdsQuery = (
	api: ActionApiReference | null | undefined,
	revisionId: string | null | undefined,
	enabled: boolean,
) => ({
	queryKey: [...KEY, 'revision-ids', api?.vendor, api?.name, api?.version, revisionId],
	queryFn: async () =>
		storedOperationIds(
			specActions(
				await getApiRevisionSpec(api!.vendor, api!.name!, api!.version!, revisionId!),
			),
		),
	enabled: enabled && ready(api) && !!revisionId,
	staleTime: STALE_MS,
});

function asRevisionIds(
	r: { data?: Map<string, string>; isSuccess: boolean },
	asked: boolean,
): RevisionOperationIds {
	// Nothing to read (no revision on the list yet, or not asked): no ids, known.
	if (!asked) return { known: true, ids: NO_IDS };
	return r.isSuccess && r.data ? { known: true, ids: r.data } : { known: false, ids: NO_IDS };
}

/**
 * The stored `operationId` of each action of one revision (`POST /orders` →
 * `createOrder`), read from that revision's own document — the one its
 * operations were parsed from. Only ids stored unchanged are kept
 * (see {@link storedOperationIds}).
 */
export function useRevisionOperationIds(
	api: ActionApiReference | null | undefined,
	revisionId: string | null | undefined,
	opts: { enabled?: boolean } = {},
): RevisionOperationIds {
	const enabled = opts.enabled ?? true;
	const { data, isSuccess } = useQuery(revisionIdsQuery(api, revisionId, enabled));
	const asked = enabled && ready(api) && !!revisionId;
	return useMemo(() => asRevisionIds({ data, isSuccess }, asked), [data, isSuccess, asked]);
}

/** {@link useRevisionOperationIds} for several APIs at once (the siblings of a rule list). */
export function useRevisionOperationIdsMany(
	targets: ReadonlyArray<{ api: ActionApiReference; revisionId: string | null | undefined }>,
	opts: { enabled?: boolean } = {},
): RevisionOperationIds[] {
	const enabled = opts.enabled ?? true;
	const results = useQueries({
		queries: targets.map((t) => revisionIdsQuery(t.api, t.revisionId, enabled)),
		combine: combineRevisionIds,
	});
	return useMemo(
		() =>
			results.map((r, i) =>
				asRevisionIds(r, enabled && ready(targets[i]?.api) && !!targets[i]?.revisionId),
			),
		[results, targets, enabled],
	);
}

/** Module-level, so `useQueries` sees one `combine` and keeps its result stable. */
function combineRevisionIds(
	results: ReadonlyArray<{ data?: Map<string, string>; isSuccess: boolean }>,
): Array<{ data?: Map<string, string>; isSuccess: boolean }> {
	return results.map((r) => ({ data: r.data, isSuccess: r.isSuccess }));
}

/**
 * The operation list with each operation's stored `operationId`
 * (`spec_operation_id`), for matching rules that name operations by it. The
 * list itself is returned until the revision's document is read.
 */
export function useOperationsWithSpecIds<
	T extends { method: string; path: string; revision_id?: string },
>(
	api: ActionApiReference | null | undefined,
	ops: readonly T[],
	opts: { enabled?: boolean } = {},
): ReadonlyArray<T & { spec_operation_id?: string | null }> {
	const spec = useRevisionOperationIds(api, ops[0]?.revision_id, opts);
	return useMemo(
		() =>
			spec.ids.size === 0
				? ops
				: ops.map((op) => ({
						...op,
						spec_operation_id: spec.ids.get(actionAt(op.method, op.path)) ?? null,
					})),
		[ops, spec],
	);
}

/**
 * What the ids of the API's earlier revisions name: each registry id (`op_…`,
 * recomputed from the revision, path and method) and each stored
 * `operationId`, mapped to the action it was. Read only when asked, as it
 * fetches one document per earlier revision (the newest ten).
 */
export function usePastActions(
	api: ActionApiReference | null | undefined,
	currentRevisionId: string | null | undefined,
	opts: { enabled?: boolean } = {},
) {
	return useQuery({
		queryKey: [...KEY, 'past', api?.vendor, api?.name, api?.version, currentRevisionId],
		queryFn: async (): Promise<Map<string, PastAction>> => {
			const { vendor, name, version } = api as {
				vendor: string;
				name: string;
				version: string;
			};
			const page = await listApiRevisions(vendor, name, version);
			const earlier = page.data
				.filter((r) => r.revision_id !== currentRevisionId)
				.sort((a, b) =>
					(b.promoted_at ?? b.created_at).localeCompare(a.promoted_at ?? a.created_at),
				)
				.slice(0, MAX_PAST_REVISIONS);
			const named = new Map<string, PastAction>();
			// Oldest first, so a newer revision's naming of an id wins.
			for (const rev of [...earlier].reverse()) {
				let doc: unknown;
				try {
					doc = await getApiRevisionSpec(vendor, name, version, rev.revision_id);
				} catch {
					continue;
				}
				const actions = specActions(doc);
				const ids = storedOperationIds(actions);
				for (const a of actions) {
					const past: PastAction = { method: a.method, path: a.path, summary: a.summary };
					named.set(await registryOperationId(rev.revision_id, a.path, a.method), past);
					const specId = ids.get(actionAt(a.method, a.path));
					if (specId) named.set(specId, past);
				}
			}
			return named;
		},
		enabled: (opts.enabled ?? true) && ready(api),
		staleTime: STALE_MS,
	});
}

/** Where else a covered API declares an `operationId`. */
export interface SharedOperationId {
	/** The other APIs that declare it ("Orders Api v2.0.0"). */
	titles: string[];
	/** Their operations' registry ids for it, in their current revisions. */
	ids: string[];
}

/** The `operationId`s other covered APIs declare too, or that it can't be told. */
export type CoveredOperationIds =
	| { status: 'loading' }
	/** Too many covered APIs, or one couldn't be read: no `operationId` is known unique. */
	| { status: 'unknown' }
	/** Each `operationId` another covered API declares, with where. */
	| { status: 'ready'; elsewhere: ReadonlyMap<string, SharedOperationId> };

const NONE_ELSEWHERE: CoveredOperationIds = { status: 'ready', elsewhere: new Map() };

/**
 * For a credential that isn't pinned to one API and version, the broker reads
 * an `operationId` against every live API the credential covers: one that
 * more than one declares is ambiguous, and then a Deny still denies but an Ask
 * refuses and an Allow grants nothing. This reads the OTHER live APIs the
 * scope covers — the vendor's, or the same API's other versions — and the
 * `operationId`s each declares, with each one's registry id in that API's
 * current revision: a rule for an ambiguous action names every one of them,
 * so it holds whichever API a call resolves to. A pinned scope covers no
 * other API.
 */
export function useCoveredOperationIds(
	api: ActionApiReference | null | undefined,
	reach: 'vendor-wide' | 'any-version' | 'pinned' | undefined,
	opts: { enabled?: boolean } = {},
): CoveredOperationIds {
	const asked = (opts.enabled ?? true) && ready(api);
	const wanted = asked && reach !== undefined && reach !== 'pinned';
	const query = useQuery({
		queryKey: [...KEY, 'covered', api?.vendor, reach === 'any-version' ? api?.name : null],
		queryFn: async () => {
			const { vendor, name } = api as { vendor: string; name: string };
			const list = await listApis({ vendor, limit: 100 });
			const others = list.data.filter(
				(a) =>
					a.current_revision_id != null &&
					(reach !== 'any-version' || a.api.name === name),
			);
			if (others.length > MAX_COVERED_APIS || list.has_more) return null;
			const declared: Array<{
				key: string;
				title: string;
				ids: Array<{ specId: string; registryId: string }>;
			}> = [];
			for (const a of others) {
				const { vendor: v, name: n, version } = a.api;
				const revision = a.current_revision_id as string;
				// The current revision's own document, which its operations (and
				// their registry ids) were parsed from.
				const doc =
					(await getApiRevisionSpec(v, n, version, revision)) ??
					(await getApiSpec(v, n, version));
				const ids: Array<{ specId: string; registryId: string }> = [];
				for (const x of specActions(doc)) {
					if (!x.operationId) continue;
					ids.push({
						specId: x.operationId,
						registryId: await registryOperationId(revision, x.path, x.method),
					});
				}
				declared.push({
					key: `${n}@${version}`,
					title: `${a.display_name || n} v${version}`,
					ids,
				});
			}
			return declared;
		},
		enabled: wanted,
		staleTime: STALE_MS,
	});
	const self = api ? `${api.name}@${api.version}` : '';
	return useMemo<CoveredOperationIds>(() => {
		// A host that doesn't say how far the credential reaches can't vouch
		// for any `operationId` being unique.
		if (asked && reach === undefined) return { status: 'unknown' };
		if (!wanted) return NONE_ELSEWHERE;
		if (query.isError || query.data === null) return { status: 'unknown' };
		if (!query.data) return { status: 'loading' };
		const elsewhere = new Map<string, SharedOperationId>();
		for (const other of query.data) {
			if (other.key === self) continue;
			for (const { specId, registryId } of other.ids) {
				const shared = elsewhere.get(specId) ?? { titles: [], ids: [] };
				if (!shared.titles.includes(other.title)) shared.titles.push(other.title);
				shared.ids.push(registryId);
				elsewhere.set(specId, shared);
			}
		}
		return { status: 'ready', elsewhere };
	}, [asked, reach, wanted, query.isError, query.data, self]);
}
