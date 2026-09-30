/**
 * WorkspacePage — the Library's full Workspace view (`/library/workspace`):
 * the APIs registered in this jentic-one instance.
 *
 * Reached by expanding the "Your workspace" panel docked beside the Catalog
 * (the two share a view-transition name, so the panel grows into this page),
 * and folded back with "Back to the catalog".
 *
 * Scoped to **APIs only** (credentials + agents live in other modules).
 * The page owns the import dialog open-state (a single dialog reachable from
 * both the header button and the empty-state CTA), an in-memory text filter
 * over the loaded rows, and a serving-state filter (All / Live / Draft /
 * Update available — all read off `GET /apis` fields) mirrored in `?status=` so
 * the Catalog panel's attention links land pre-filtered.
 */
import { useMemo, useState } from 'react';
import { useConsumedFlagParam } from '@/shared/hooks';
import { useSearchParams } from 'react-router';
import { Upload } from 'lucide-react';
import {
	PageShell,
	PageHeader,
	PageHelp,
	Button,
	BackButton,
	ErrorAlert,
	SegmentedToggle,
	apiServingState,
} from '@/shared/ui';
import { newestFirst } from '@/shared/lib/newestFirst';
import { ROUTES } from '@/shared/app/routes';
import { libraryWorkspaceVtStyle } from '@/shared/app/viewTransitions';
import { ApiGrid } from '@/modules/workspace/components/ApiGrid';
import { ImportSpecDialog } from '@/shared/credentials/components/ImportSpecDialog';
import { WorkspaceStatsStrip } from '@/modules/workspace/components/WorkspaceStatsStrip';
import { WorkspaceFilterBar } from '@/modules/workspace/components/WorkspaceFilterBar';
import { WorkspaceCatalogFooter } from '@/modules/workspace/components/WorkspaceCatalogFooter';
import { useAllWorkspaceApis, workspaceApiDisplayTitle } from '@/modules/workspace/api';
import type { WorkspaceApi } from '@/modules/workspace/api';

type StatusFilter = 'all' | 'live' | 'draft' | 'update';

const STATUS_FILTERS: StatusFilter[] = ['all', 'live', 'draft', 'update'];

function isStatusFilter(value: string | null): value is StatusFilter {
	return STATUS_FILTERS.includes(value as StatusFilter);
}

function matchesStatus(api: WorkspaceApi, status: StatusFilter): boolean {
	return status === 'all' || apiServingState(api).states.includes(status);
}

export default function WorkspacePage() {
	const [searchParams, setSearchParams] = useSearchParams();
	// `?import=1` opens the import dialog on arrival — landing on the Workspace
	// is the point (the new API appears in the list behind it).
	const [importOpen, setImportOpen] = useConsumedFlagParam('import');
	// `?q=` seeds the text filter (a shared / reloaded filtered view).
	const [filter, setFilter] = useState(() => searchParams.get('q') ?? '');
	const statusParam = searchParams.get('status');
	const status: StatusFilter = isStatusFilter(statusParam) ? statusParam : 'all';
	const list = useAllWorkspaceApis();

	function setStatus(next: StatusFilter) {
		setSearchParams(
			(prev) => {
				const params = new URLSearchParams(prev);
				if (next === 'all') params.delete('status');
				else params.set('status', next);
				return params;
			},
			{ replace: true },
		);
	}

	// Mirror the text filter back into `?q=` (set when non-empty, dropped when
	// cleared) so the URL stays shareable/reloadable — same round-trip as
	// `?status=` above, rather than a one-way seed.
	function setFilterAndUrl(next: string) {
		setFilter(next);
		setSearchParams(
			(prev) => {
				const params = new URLSearchParams(prev);
				const trimmed = next.trim();
				if (trimmed) params.set('q', trimmed);
				else params.delete('q');
				return params;
			},
			{ replace: true },
		);
	}

	const apis = list.isPending ? undefined : list.items;
	const counts = useMemo(() => {
		const rows = apis ?? [];
		return {
			live: rows.filter((a) => matchesStatus(a, 'live')).length,
			draft: rows.filter((a) => matchesStatus(a, 'draft')).length,
			update: rows.filter((a) => matchesStatus(a, 'update')).length,
		};
	}, [apis]);

	const filtered = useMemo(() => {
		// Newest import first (`GET /apis` → `created_at`) — the same order as
		// the docked panel on the Catalog, so expanding it doesn't reshuffle.
		const rows = (apis ?? [])
			.filter((api) => matchesStatus(api, status))
			.sort(
				newestFirst((a, b) =>
					workspaceApiDisplayTitle(a).localeCompare(workspaceApiDisplayTitle(b)),
				),
			);
		const needle = filter.trim().toLowerCase();
		if (!needle) return rows;
		return rows.filter((api) => {
			const haystack = [
				api.displayName ?? '',
				api.description ?? '',
				api.api.vendor,
				api.api.name,
				api.api.host ?? '',
				api.catalogApiId ?? '',
			]
				.join(' ')
				.toLowerCase();
			return haystack.includes(needle);
		});
	}, [apis, filter, status]);

	const total = apis?.length ?? 0;
	// Nothing to narrow: disable both filters so the real empty state (and its
	// Import button) shows instead of a "No matches" over an empty workspace.
	const noApis = !list.isPending && list.items.length === 0;
	const isFiltering = !noApis && (filter.trim().length > 0 || status !== 'all');
	const resultsLabel = isFiltering ? `${filtered.length} of ${total}` : undefined;

	// Counts only once every page answered — never a "· 0" while loading.
	const countSuffix = (n: number) => (list.complete ? ` · ${n}` : '');
	const statusOptions = [
		{ value: 'all' as const, label: 'All' },
		{ value: 'live' as const, label: `Live${countSuffix(counts.live)}` },
		{ value: 'draft' as const, label: `Draft${countSuffix(counts.draft)}` },
		...(counts.update > 0 || status === 'update'
			? [{ value: 'update' as const, label: `Update available${countSuffix(counts.update)}` }]
			: []),
	];

	const importButton = (
		<Button
			variant="outline"
			size="sm"
			onClick={() => setImportOpen(true)}
			data-testid="workspace-import-open"
		>
			<Upload size={14} aria-hidden="true" />
			Import your own API
		</Button>
	);

	return (
		<PageShell>
			<PageHeader
				title="Your workspace"
				subtitle="The APIs registered in this instance — part of your Library."
				actions={
					<>
						{importButton}
						<PageHelp
							title="About your workspace"
							sections={[
								{
									heading: 'What lives here',
									body: 'Every API you have imported into this jentic-one instance — its operations, versions, and security schemes. Click an API to open its hub.',
								},
								{
									heading: 'Adding an API',
									body: 'Use "Import your own API" to register an OpenAPI spec by URL, paste, or file upload — or import one from the public catalog. Imports run server-side; a freshly imported API may start as a draft revision you promote to make its operations live.',
								},
								{
									heading: 'Filtering',
									body: 'The filter box narrows the APIs shown right now — an in-memory match over name, description, and vendor. The Live / Draft / Update toggle narrows by serving state. To search the public catalog, go back to the Catalog.',
								},
							]}
						/>
					</>
				}
			/>

			{/* Same placement as the detail pages' BackButton (directly under the
			    header, a static link — the Catalog is this page's parent, not
			    "wherever you came from"). */}
			<BackButton
				to={ROUTES.library}
				label="Back to the catalog"
				useHistory={false}
				className="-mt-2"
				testId="workspace-back-to-catalog"
			/>

			<div style={libraryWorkspaceVtStyle} className="space-y-6">
				<WorkspaceStatsStrip apis={apis ?? []} loading={list.isPending} />

				<WorkspaceFilterBar
					value={filter}
					onChange={setFilterAndUrl}
					resultsLabel={resultsLabel}
					disabled={noApis}
					trailing={
						<SegmentedToggle
							options={statusOptions}
							value={status}
							onChange={setStatus}
							ariaLabel="Filter by serving state"
							disabled={noApis}
						/>
					}
				/>

				{/* A later page failed: keep the rows that loaded, flag the gap. */}
				{list.error && list.items.length > 0 ? (
					<ErrorAlert message={list.error} onRetry={list.retry} />
				) : null}

				<ApiGrid
					apis={filtered}
					isLoading={list.isPending}
					isError={list.error != null && list.items.length === 0}
					error={list.error}
					onRetry={list.retry}
					emptyAction={importButton}
					filtered={isFiltering}
				/>

				<WorkspaceCatalogFooter />
			</div>

			<ImportSpecDialog open={importOpen} onClose={() => setImportOpen(false)} />
		</PageShell>
	);
}
