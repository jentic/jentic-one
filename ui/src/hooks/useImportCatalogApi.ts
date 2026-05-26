/**
 * useImportCatalogApi
 *
 * Imports a directory (catalog) API into the local workspace via
 * `POST /import` — *without* forcing the user through the credential
 * form. The previous "Import to workspace" CTA opened
 * `/credentials/new?api_id=…` which conflated two distinct user
 * intents:
 *
 *   1. "Make this API available locally so I can browse its operations
 *      and decide what to do with it."  → just import.
 *   2. "Make this API runnable end-to-end against my account."        → import + credential.
 *
 * The button is labelled "Import to workspace", so it should do (1).
 * Credentials can be added afterwards from Workspace.
 *
 * Mechanics:
 *   - Prefer the `spec_url` already on the entity (server-side change
 *     surfaces it on `/apis` and `/search` catalog rows for free).
 *   - Fall back to `GET /catalog/{api_id}` for older cached entities
 *     that pre-date the spec_url field — keeps the hook safe to call
 *     during a deploy window where stale React Query payloads are still
 *     in memory.
 *   - On success, emit `credentialImported` so the existing toast +
 *     query-invalidation pipeline in `DiscoveryView` /
 *     `WorkspaceView` reacts (the event channel is misnamed; rename
 *     tracked in #437). Workflows that ship with the API are
 *     auto-imported by the backend; we don't fan out a second mutation.
 *
 * Returns a stable `import` function plus loading flag so the calling
 * component can disable its button while the request is in flight.
 */

import { useCallback } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';
import { emitCredentialImported } from '@/lib/events/credentialImported';
import { toast } from '@/components/ui/toastStore';

export interface ImportCatalogApiArgs {
	apiId: string;
	/** When known (from the catalog/search row) skips the
	 *  `getCatalogEntry` round-trip. */
	specUrl?: string;
}

interface UseImportCatalogApiResult {
	importApi: (args: ImportCatalogApiArgs) => Promise<void>;
	isImporting: boolean;
	pendingApiId: string | null;
}

export function useImportCatalogApi(): UseImportCatalogApiResult {
	const queryClient = useQueryClient();

	const mutation = useMutation({
		mutationFn: async ({ apiId, specUrl }: ImportCatalogApiArgs) => {
			let resolvedSpecUrl = specUrl;
			if (!resolvedSpecUrl) {
				const entry = (await api.getCatalogEntry(apiId)) as {
					spec_url?: string | null;
					spec_error?: string | null;
				};
				if (entry.spec_error) {
					throw new Error(entry.spec_error);
				}
				if (!entry.spec_url) {
					throw new Error(
						`Couldn't locate an OpenAPI spec for ${apiId} in the public catalog.`,
					);
				}
				resolvedSpecUrl = entry.spec_url;
			}

			const safeId = apiId.replace(/\//g, '_');
			const res = (await api.importSpec([
				{
					type: 'url',
					url: resolvedSpecUrl,
					force_api_id: apiId,
					filename: `${safeId}_openapi.json`,
				},
			])) as {
				results?: Array<{
					status?: string;
					error?: string;
					api_id?: string;
				}>;
			};
			const result = res.results?.[0];
			if (!result || result.status !== 'success') {
				throw new Error(result?.error ?? `Import failed for ${apiId}.`);
			}
			return result.api_id ?? apiId;
		},
		onSuccess: (importedId, variables) => {
			const apiId = importedId || variables.apiId;
			queryClient.invalidateQueries({ queryKey: ['apis'] });
			queryClient.invalidateQueries({ queryKey: ['catalog'] });
			queryClient.invalidateQueries({ queryKey: ['apis', 'discover'] });
			queryClient.invalidateQueries({ queryKey: ['sheet-resolve-source'] });
			queryClient.invalidateQueries({ queryKey: ['workspace'] });
			queryClient.invalidateQueries({ queryKey: ['workspace-stats'] });

			// Re-uses the existing cross-tab event so the rest of the
			// UX (toast in DiscoveryView, "Open in Workspace" deep link,
			// etc.) just works. The event name is historical — see
			// `src/lib/events/credentialImported.ts` and #437.
			emitCredentialImported({ api_id: apiId });
		},
		onError: (err: unknown, variables) => {
			toast({
				title: 'Import failed',
				description:
					err instanceof Error
						? err.message
						: `Couldn't import ${variables.apiId} from the public catalog.`,
				variant: 'error',
			});
		},
	});

	const importApi = useCallback(
		async (args: ImportCatalogApiArgs) => {
			await mutation.mutateAsync(args);
		},
		[mutation],
	);

	return {
		importApi,
		isImporting: mutation.isPending,
		pendingApiId: mutation.isPending
			? ((mutation.variables as ImportCatalogApiArgs | undefined)?.apiId ?? null)
			: null,
	};
}
