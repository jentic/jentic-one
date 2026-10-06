/**
 * The GitHub pick the zero-agents landing offers a newly approved agent: the
 * workspace's own GitHub API when one is registered, else GitHub's REST API from
 * the public catalog, which the setup queue imports on the way through.
 *
 * The catalog is found the way the Add-APIs picker finds it — a `github` search
 * (the catalog lists nothing for an empty query in the picker, and browsing
 * 6k+ entries to find one would be absurd). The public catalog has no bare
 * `github.com` entry: GitHub's REST API is the umbrella id
 * `github.com/api.github.com` (also mirrored as `api.github.com`), next to the
 * Enterprise Cloud flavour `github.com/ghec` and many unrelated `*.github.io`
 * hosts that a `github` search also returns.
 */
import { useMemo } from 'react';
import {
	apiRowToSelected,
	catalogToSelected,
	useAllApis,
	useCatalog,
	type ApiResponse,
	type SelectedApi,
} from '@/shared/credentials/api';

/** The picker's own search term, so the landing shares its cached results. */
const GITHUB_CATALOG_QUERY = 'github';

/** GitHub's REST API in the public catalog, most canonical first. */
const GITHUB_REST_CATALOG_IDS = ['github.com/api.github.com', 'api.github.com'] as const;

/** A GitHub vendor as the catalog (`github.com`) or a workspace slug writes it. */
const GITHUB_VENDOR = /^(api[.-])?github([.-]com)?$/i;

/** GitHub flavours that are not the plain REST API. */
const NOT_REST = /ghec|enterprise/i;

/**
 * Rank a catalog entry as GitHub's REST API: the known ids first, then any
 * other entry the catalog files under GitHub's own vendor. `null` = not it
 * (Enterprise Cloud, or a `*.github.io` host that merely contains the word).
 */
function githubRank(apiId: string, vendor: string | null | undefined): number | null {
	const known = (GITHUB_REST_CATALOG_IDS as readonly string[]).indexOf(apiId);
	if (known !== -1) return known;
	if (NOT_REST.test(apiId)) return null;
	return vendor && GITHUB_VENDOR.test(vendor) ? GITHUB_REST_CATALOG_IDS.length : null;
}

/** The best GitHub REST match in `items`, by `githubRank`. */
function bestGithub<T>(items: T[], rank: (item: T) => number | null): T | null {
	let best: T | null = null;
	let bestRank = Infinity;
	for (const item of items) {
		const r = rank(item);
		if (r != null && r < bestRank) {
			best = item;
			bestRank = r;
		}
	}
	return best;
}

/** A workspace row is GitHub's REST API by its catalog origin or its vendor. */
function workspaceRank(row: ApiResponse): number | null {
	if (row.catalog_api_id) return githubRank(row.catalog_api_id, row.api.vendor);
	return GITHUB_VENDOR.test(row.api.vendor) ? GITHUB_REST_CATALOG_IDS.length : null;
}

export interface GithubPick {
	/** `null` while loading, and when neither source has GitHub. */
	pick: SelectedApi | null;
	/** The pick isn't known yet. A workspace GitHub settles it at once; without
	 * one, both the workspace drain and the catalog search must answer. */
	loading: boolean;
	/** The catalog search failed or returned nothing at all — no catalog to offer
	 * from yet (not merely "no GitHub in it"). */
	catalogUnavailable: boolean;
}

export function useGithubPick(): GithubPick {
	const apis = useAllApis();
	const catalog = useCatalog(GITHUB_CATALOG_QUERY);

	const local = useMemo(() => {
		const row = bestGithub(apis.items, workspaceRank);
		if (!row) return null;
		const picked = apiRowToSelected(row);
		// A user-set display name wins; otherwise title it as the landing names it.
		return row.display_name?.trim() ? picked : { ...picked, label: 'GitHub' };
	}, [apis.items]);

	const fromCatalog = useMemo(() => {
		const entry = bestGithub(catalog.data?.data ?? [], (e) => githubRank(e.api_id, e.vendor));
		// Titled as the landing names it: the catalog title of the umbrella id is
		// its sub-segment (`api.github.com`), which reads as a host, not GitHub.
		return entry ? { ...catalogToSelected(entry), label: 'GitHub' } : null;
	}, [catalog.data]);

	const loading = local == null && ((!apis.complete && !apis.error) || catalog.isPending);
	return {
		pick: loading ? null : (local ?? fromCatalog),
		loading,
		catalogUnavailable: catalog.isError || (catalog.data?.catalog_total ?? 1) === 0,
	};
}
