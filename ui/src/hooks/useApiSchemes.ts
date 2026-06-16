import { useQuery } from '@tanstack/react-query';
import type { ApiOut } from '@/api/types';
import { api } from '@/api/client';
import type { RawSchemes } from '@/lib/credentials/schemes';

/**
 * Resolve security schemes for an API selected in the credential form.
 *
 * Two paths, depending on `selectedApi.source`:
 *
 *  - **local** — the workspace already has the API row, and our backend
 *    materialised `security_schemes` on it during import. We just GET
 *    `/apis/{id}` and read the field. Cheap and authoritative.
 *  - **catalog** — the API is a Jentic-public-catalog row that hasn't
 *    been imported yet. We don't have the spec locally, so we hit the
 *    catalog metadata endpoint, follow `spec_url` (a public GitHub URL),
 *    and parse `components.securitySchemes` straight off the YAML/JSON.
 *    The spec fetch is staleTime-cached for 5 minutes so flipping
 *    between auth methods on the form doesn't re-download.
 *
 *  - For an API row with no `source` field (legacy seed data, very
 *    early imports), we fall through the local path because that's the
 *    safer default — local detail is always present once the row is in
 *    `/apis`.
 *
 * Returns `localDetail` and `spec` separately because downstream code
 * (`useApiServerVarDefs`) needs them for a different concern (server
 * variables) — re-fetching them there would double the network calls.
 */
export function useApiSchemes(selectedApi: ApiOut | null): {
	schemes: RawSchemes;
	loading: boolean;
	localDetail: ApiOut | null;
	spec: any;
} {
	const isCatalog = selectedApi?.source === 'catalog';
	const isLocal = selectedApi?.source === 'local' || (!!selectedApi && !selectedApi.source);

	const { data: localDetail, isLoading: localLoading } = useQuery({
		queryKey: ['api-detail', selectedApi?.id],
		queryFn: () => api.getApi(selectedApi!.id),
		enabled: !!selectedApi && isLocal,
	});

	const { data: catalogEntry, isLoading: entryLoading } = useQuery({
		queryKey: ['catalog-entry', selectedApi?.id],
		queryFn: () => api.getCatalogEntry(selectedApi!.id),
		enabled: !!selectedApi && isCatalog,
	});

	const specUrl: string | null = (catalogEntry as any)?.spec_url ?? null;

	const { data: spec, isLoading: specLoading } = useQuery({
		queryKey: ['spec', specUrl],
		queryFn: async () => {
			// Abort the spec fetch if the (public, third-party) host is slow so the
			// credential form never hangs indefinitely waiting on it.
			const controller = new AbortController();
			const timeout = setTimeout(() => controller.abort(), 10_000);
			try {
				const res = await fetch(specUrl!, { signal: controller.signal });
				if (!res.ok) throw new Error(`Failed to fetch spec: ${res.status}`);
				return await res.json();
			} finally {
				clearTimeout(timeout);
			}
		},
		enabled: !!specUrl,
		staleTime: 5 * 60 * 1000,
	});

	if (isLocal) {
		const schemes = (localDetail as any)?.security_schemes as RawSchemes;
		return {
			schemes,
			loading: localLoading,
			localDetail: (localDetail as ApiOut) ?? null,
			spec: null,
		};
	}

	if (isCatalog) {
		const schemes = (spec as any)?.components?.securitySchemes as RawSchemes;
		return {
			schemes,
			loading: entryLoading || specLoading,
			localDetail: null,
			spec: spec ?? null,
		};
	}

	return { schemes: null, loading: false, localDetail: null, spec: null };
}
