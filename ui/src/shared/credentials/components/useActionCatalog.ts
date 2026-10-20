import { useMemo } from 'react';
import { useVendorOperationsProgress } from '@/shared/credentials/api/vendors-hooks';
import type { ActionCatalog } from '@/shared/credentials/lib/actionRules';
import type { OpsApiReference } from '@/shared/credentials/components/OperationImpactPreview';

const NO_LIST: ActionCatalog = { status: 'unavailable', operations: [] };

/**
 * The API's operation list as an {@link ActionCatalog}, for naming an action
 * rule's operation ids. Shares the operations cache with the effective-access
 * preview and the By action checklist, so it adds no read of its own.
 *
 * `pending` holds the catalog at `loading` while the host knows the list is
 * coming but can't read it yet (the API is still importing).
 */
export function useActionCatalog(
	api: OpsApiReference | null | undefined,
	opts: { enabled?: boolean; pending?: boolean } = {},
): ActionCatalog {
	const ready = !!api && !!api.name && !!api.version;
	const enabled = (opts.enabled ?? true) && ready;
	const load = useVendorOperationsProgress(ready ? api : undefined, { enabled });
	const { query, operations, complete } = load;
	// A finished read with no list (404: no current revision) or a failed one.
	const noList = query.isError || (query.isFetched && query.data === null);
	const pending = opts.pending ?? false;
	return useMemo<ActionCatalog>(() => {
		if (complete) return { status: 'ready', operations };
		if (!ready) return pending ? { status: 'loading', operations: [] } : NO_LIST;
		if (noList && !pending) return { status: 'unavailable', operations };
		if (!enabled && !pending && operations.length === 0) return NO_LIST;
		return { status: 'loading', operations };
	}, [complete, operations, ready, pending, noList, enabled]);
}
