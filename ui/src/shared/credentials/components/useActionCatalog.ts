import { useMemo } from 'react';
import {
	useVendorOperationsMany,
	useVendorOperationsProgress,
} from '@/shared/credentials/api/vendors-hooks';
import {
	useCoveredOperationIds,
	usePastActions,
	useRevisionOperationIds,
	useRevisionOperationIdsMany,
	type CoveredOperationIds,
	type RevisionOperationIds,
} from '@/shared/credentials/api/action-identity-hooks';
import type { VendorOperation } from '@/shared/credentials/api/vendors-client';
import {
	type ActionCatalog,
	type ActionOperation,
	type SiblingActionCatalog,
} from '@/shared/credentials/lib/actionRules';
import type { ScopeReach } from '@/shared/credentials/lib/accessPresets';
import { actionAt } from '@/shared/credentials/lib/specOperationIds';
import type { OpsApiReference } from '@/shared/credentials/components/OperationImpactPreview';

const NO_LIST: ActionCatalog = { status: 'unavailable', operations: [] };

/** Another API the same rule list governs, for naming its actions. */
export interface SiblingApiReference {
	/** Stable key (the host's tile key). */
	key: string;
	title: string;
	api: OpsApiReference;
}

const NO_SIBLINGS: readonly SiblingApiReference[] = [];
const NO_IDS: readonly string[] = [];

/**
 * Each operation with what its spec says: its stored `operationId`, and the
 * ids a rule set here names it by — the `operationId` when the credential's
 * other APIs don't declare it too, else the registry ids of the action in
 * this API AND in every covered API that shares it, so the rule holds
 * whichever one a call resolves to (as when the spec's ids aren't read yet,
 * this API's registry id alone). An ambiguous one says which APIs share it.
 */
function withSpecIds(
	ops: readonly VendorOperation[],
	spec: RevisionOperationIds,
	covered: CoveredOperationIds | null,
): ActionOperation[] {
	if (spec.ids.size === 0) return ops as ActionOperation[];
	return ops.map((op): ActionOperation => {
		const specId = spec.ids.get(actionAt(op.method, op.path)) ?? null;
		if (!specId) return { ...op, spec_operation_id: null };
		const shared = covered?.status === 'ready' ? (covered.elsewhere.get(specId) ?? null) : null;
		const usable = covered?.status === 'ready' && !shared;
		return {
			...op,
			spec_operation_id: specId,
			action_key: usable ? specId : op.operation_id,
			...(shared && {
				action_keys: [op.operation_id, ...shared.ids],
				ambiguous_with: shared.titles.join(', '),
			}),
		};
	});
}

/**
 * The API's operation list as an {@link ActionCatalog}, for naming an action
 * rule's ids and choosing the id a rule set here writes. Shares the
 * operations cache with the effective-access preview and the By action
 * checklist.
 *
 * `pending` holds the catalog at `loading` while the host knows the list is
 * coming but can't read it yet (the API is still importing).
 *
 * `siblings` are the other APIs the same rule list governs (one credential
 * reaching several APIs shares one binding, so one rule list). Their lists
 * ride along as `elsewhere`, so an id that belongs to one of them is named as
 * that API's action rather than as missing.
 *
 * `reach` is how far the credential reaches: a pinned one makes every
 * `operationId` unique; a wider one checks the other APIs it covers. Without
 * it no `operationId` is vouched for, and rules keep the registry id.
 *
 * `ruleIds` are the ids the host's rules hold: when one names nothing
 * current, the API's earlier revisions are read to say what it was.
 */
export function useActionCatalog(
	api: OpsApiReference | null | undefined,
	opts: {
		enabled?: boolean;
		pending?: boolean;
		siblings?: readonly SiblingApiReference[];
		reach?: ScopeReach;
		ruleIds?: readonly string[];
	} = {},
): ActionCatalog {
	const ready = !!api && !!api.name && !!api.version;
	const enabled = (opts.enabled ?? true) && ready;
	const load = useVendorOperationsProgress(ready ? api : undefined, { enabled });
	const { query, operations, complete } = load;
	// A finished read with no list (404: no current revision) or a failed one.
	const noList = query.isError || (query.isFetched && query.data === null);
	const pending = opts.pending ?? false;
	const siblings = opts.siblings ?? NO_SIBLINGS;
	const siblingRefs = useMemo(() => siblings.map((s) => s.api), [siblings]);
	const siblingLists = useVendorOperationsMany(siblingRefs, {
		enabled: (opts.enabled ?? true) && siblings.length > 0,
	});

	// The current revision's own document names each operation's `operationId`.
	const revisionId = operations[0]?.revision_id;
	const spec = useRevisionOperationIds(api, revisionId, { enabled });
	const covered = useCoveredOperationIds(api, opts.reach, {
		enabled: enabled && spec.ids.size > 0,
	});
	const siblingTargets = useMemo(
		() =>
			siblings.map((s, i) => ({
				api: s.api,
				revisionId: siblingLists[i]?.operations[0]?.revision_id,
			})),
		[siblings, siblingLists],
	);
	const siblingSpecs = useRevisionOperationIdsMany(siblingTargets, {
		enabled: (opts.enabled ?? true) && siblings.length > 0,
	});

	const ownOps = useMemo(
		() => withSpecIds(operations, spec, covered),
		[operations, spec, covered],
	);
	// An id the rules hold that no current operation answers to: read what the
	// earlier revisions named, once the list is whole.
	const ruleIds = opts.ruleIds ?? NO_IDS;
	const wantsPast = useMemo(() => {
		if (!complete || !spec.known || ruleIds.length === 0) return false;
		const current = new Set(ownOps.flatMap((op) => [op.operation_id, op.spec_operation_id]));
		for (const l of siblingLists) for (const op of l.operations) current.add(op.operation_id);
		for (const s of siblingSpecs) for (const id of s.ids.values()) current.add(id);
		return ruleIds.some((id) => !current.has(id));
	}, [complete, spec.known, ruleIds, ownOps, siblingLists, siblingSpecs]);
	const past = usePastActions(api, revisionId, { enabled: enabled && wantsPast });

	const elsewhere = useMemo<SiblingActionCatalog[] | undefined>(
		() =>
			siblings.length === 0
				? undefined
				: siblings.map((s, i) => ({
						key: s.key,
						title: s.title,
						status: siblingLists[i]?.status ?? 'loading',
						operations: withSpecIds(
							siblingLists[i]?.operations ?? [],
							siblingSpecs[i] ?? { known: false, ids: new Map() },
							null,
						),
						specIdsKnown: siblingSpecs[i]?.known ?? false,
					})),
		[siblings, siblingLists, siblingSpecs],
	);
	return useMemo<ActionCatalog>(() => {
		const own = ((): ActionCatalog => {
			if (complete) return { status: 'ready', operations: ownOps };
			if (!ready) return pending ? { status: 'loading', operations: [] } : NO_LIST;
			if (noList && !pending) return { status: 'unavailable', operations: ownOps };
			if (!enabled && !pending && operations.length === 0) return NO_LIST;
			return { status: 'loading', operations: ownOps };
		})();
		return {
			...own,
			specIdsKnown: spec.known,
			...(past.data && { past: past.data }),
			...(elsewhere && { elsewhere }),
		};
	}, [
		complete,
		ownOps,
		operations.length,
		ready,
		pending,
		noList,
		enabled,
		spec.known,
		past.data,
		elsewhere,
	]);
}
