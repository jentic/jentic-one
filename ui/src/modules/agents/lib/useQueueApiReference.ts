/**
 * The workspace API whose operations the setup queue's ② Access reads: the pick
 * itself, the workspace API a catalog pick imported as (live revision, then highest
 * version, wins), or the one its import job registers. `pending` holds while those
 * are read, for a bounded time after an import.
 */
import { useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
	invalidateApiLists,
	jobSucceeded,
	pollJobToTerminal,
	useAllApis,
	type ApiResponse,
	type SelectedApi,
} from '@/shared/credentials/api';
import { getImportedApiRefs } from '@/shared/credentials/api/apis';
import { useVendorOperations } from '@/shared/credentials/api/vendors-hooks';
import { catalogImportRef, workspaceApiForCredential } from '@/shared/credentials/lib/apiIdentity';

export interface ApiReference {
	vendor: string;
	name: string;
	version: string;
}

/** How long to wait for operations after an import lands (polling continues). */
const POST_IMPORT_WAIT_MS = 20_000;

/** The pick, shaped like a credential for the matcher. */
function catalogPickIdentity(api: SelectedApi) {
	return {
		catalog_api_id: api.apiId ?? null,
		api: catalogImportRef({ apiId: api.apiId ?? api.name, vendor: api.vendor }) ?? {
			vendor: api.vendor,
			name: null,
		},
	};
}

/** Live revisions first, then the highest version: the matcher takes the first hit. */
function preferredFirst(apis: readonly ApiResponse[]): ApiResponse[] {
	return [...apis].sort((a, b) => {
		const live = Number(b.current_revision_id != null) - Number(a.current_revision_id != null);
		if (live !== 0) return live;
		return b.api.version.localeCompare(a.api.version, undefined, { numeric: true });
	});
}

export function useQueueApiReference(
	api: SelectedApi,
	opts: { importJobId?: string; active?: boolean } = {},
): { reference: ApiReference | null; pending: boolean } {
	const { importJobId, active = true } = opts;
	const queryClient = useQueryClient();
	const catalog = api.source === 'catalog';
	const apis = useAllApis();

	const workspaceRef = useMemo<ApiReference | null>(() => {
		if (!catalog) return null;
		const hit = workspaceApiForCredential(catalogPickIdentity(api), preferredFirst(apis.items));
		return hit
			? { vendor: hit.api.vendor, name: hit.api.name, version: hit.api.version }
			: null;
	}, [catalog, api, apis.items]);

	const job = useQuery<ApiReference | null>({
		queryKey: ['agents', 'setup-queue', 'import', importJobId ?? ''],
		enabled: catalog && !!importJobId && workspaceRef == null,
		queryFn: async () => {
			const jobId = importJobId as string;
			const status = await pollJobToTerminal({ jobId, status: 'queued', error: null });
			if (!jobSucceeded(status)) return null;
			// Refresh the list so the match takes over; the job's result names it meanwhile.
			invalidateApiLists(queryClient);
			const refs = await getImportedApiRefs(jobId).catch(() => []);
			const rows = refs.map((ref) => ({ catalog_api_id: null, api: ref }));
			// A lone result is the pick; among several, only a match is.
			const hit =
				workspaceApiForCredential(catalogPickIdentity(api), rows) ??
				(rows.length === 1 ? rows[0] : null);
			return hit ? { ...hit.api } : null;
		},
		staleTime: Infinity,
		retry: false,
	});

	const localRef =
		!catalog && api.version
			? { vendor: api.vendor, name: api.name, version: api.version }
			: null;
	const reference = localRef ?? workspaceRef ?? job.data ?? null;

	// After an import, the cached operations read may say "not imported yet" briefly.
	const importedHere = catalog && workspaceRef == null && job.data != null;
	const ops = useVendorOperations(reference ?? undefined, {
		enabled: active && importedHere,
	});
	const [gaveUp, setGaveUp] = useState(false);
	useEffect(() => {
		if (!importedHere) return;
		const timer = setTimeout(() => setGaveUp(true), POST_IMPORT_WAIT_MS);
		return (): void => clearTimeout(timer);
	}, [importedHere]);
	const awaitingOps = importedHere && !gaveUp && ops.data == null && !ops.isError;

	const pending =
		catalog &&
		workspaceRef == null &&
		(apis.isPending || (!!importJobId && job.isFetching) || awaitingOps);

	return { reference, pending };
}
