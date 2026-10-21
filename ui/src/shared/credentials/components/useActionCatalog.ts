import { useMemo } from 'react';
import {
	useVendorOperationsMany,
	useVendorOperationsProgress,
} from '@/shared/credentials/api/vendors-hooks';
import type { ActionCatalog, SiblingActionCatalog } from '@/shared/credentials/lib/actionRules';
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

/**
 * The API's operation list as an {@link ActionCatalog}, for naming an action
 * rule's operation ids. Shares the operations cache with the effective-access
 * preview and the By action checklist, so it adds no read of its own.
 *
 * `pending` holds the catalog at `loading` while the host knows the list is
 * coming but can't read it yet (the API is still importing).
 *
 * `siblings` are the other APIs the same rule list governs (one credential
 * reaching several APIs shares one binding, so one rule list). Their lists
 * ride along as `elsewhere`, so an id that belongs to one of them is named as
 * that API's action rather than as missing.
 */
export function useActionCatalog(
	api: OpsApiReference | null | undefined,
	opts: {
		enabled?: boolean;
		pending?: boolean;
		siblings?: readonly SiblingApiReference[];
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
	const elsewhere = useMemo<SiblingActionCatalog[] | undefined>(
		() =>
			siblings.length === 0
				? undefined
				: siblings.map((s, i) => ({
						key: s.key,
						title: s.title,
						status: siblingLists[i]?.status ?? 'loading',
						operations: siblingLists[i]?.operations ?? [],
					})),
		[siblings, siblingLists],
	);
	return useMemo<ActionCatalog>(() => {
		const own = ((): ActionCatalog => {
			if (complete) return { status: 'ready', operations };
			if (!ready) return pending ? { status: 'loading', operations: [] } : NO_LIST;
			if (noList && !pending) return { status: 'unavailable', operations };
			if (!enabled && !pending && operations.length === 0) return NO_LIST;
			return { status: 'loading', operations };
		})();
		return elsewhere ? { ...own, elsewhere } : own;
	}, [complete, operations, ready, pending, noList, enabled, elsewhere]);
}
