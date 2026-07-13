/**
 * Discover page — the public API catalog surface.
 *
 * Browses the public Jentic catalog (`GET /catalog`): each entry carries a
 * `registered` flag (Imported vs Available). The user can search, filter by
 * registration state, infinite-scroll the keyset-paginated feed, preview an
 * API's operations in a detail sheet, and import an available API into the
 * local registry. There is no server-side blend with local APIs — the catalog's
 * own per-entry `registered` boolean is the source of truth (D-005a).
 *
 * Layering: this view reaches the backend only through the module's own
 * `api/hooks` (useDiscoverCatalog / useImportCatalogApi / useOperationPreview).
 * It never touches `@/shared/api` directly (ESLint-enforced).
 */
import { useEffect, useState } from 'react';
import { PageShell, PageHeader, PageHelp } from '@/shared/ui';
import { DiscoverToolbar } from '@/modules/discover/components/DiscoverToolbar';
import { DiscoveryGrid } from '@/modules/discover/components/DiscoveryGrid';
import { ApiDetailSheet } from '@/modules/discover/components/ApiDetailSheet';
import { DiscoverStatusRow } from '@/modules/discover/components/DiscoverStatusRow';
import { useDebouncedValue } from '@/modules/discover/lib/useDebouncedValue';
import {
	useDiscoverCatalog,
	useImportCatalogApi,
	useRefreshCatalog,
	type CatalogFilter,
	type DiscoveryEntity,
} from '@/modules/discover/api';

export default function DiscoverPage() {
	const [query, setQuery] = useState('');
	const [filter, setFilter] = useState<CatalogFilter>('all');
	const [selected, setSelected] = useState<DiscoveryEntity | null>(null);
	const [sheetOpen, setSheetOpen] = useState(false);

	const debouncedQuery = useDebouncedValue(query, 250);
	const { importEntity, pendingApiIds, hasPendingImports, reconcileImported } =
		useImportCatalogApi();
	const catalog = useDiscoverCatalog({
		q: debouncedQuery,
		filter,
		pollWhilePending: hasPendingImports,
	});
	const { refresh, isRefreshing } = useRefreshCatalog();

	// When the (polled) feed updates, resolve any pending import whose entry has
	// flipped to registered — clears the card's "Importing…" state + toasts.
	useEffect(() => {
		reconcileImported(catalog.entities);
	}, [catalog.entities, reconcileImported]);

	function handleOpen(entity: DiscoveryEntity) {
		setSelected(entity);
		setSheetOpen(true);
	}

	// Prefer the live catalog row for the open sheet so its footer reflects a
	// poll-driven Available → Imported flip; fall back to the opened snapshot
	// (e.g. if the row scrolled out of the loaded pages).
	const sheetEntity =
		(selected && catalog.entities.find((e) => e.id === selected.id)) ?? selected;

	return (
		<PageShell spacing="space-y-0">
			<PageHeader
				title="Discover"
				subtitle="Browse the public Jentic catalog. Import an API to use it in your workspace."
				actions={
					<PageHelp
						title="About Discover"
						intro={
							<p>
								Discover lists the public Jentic catalog of importable APIs,
								flagging which are already imported into your workspace.
							</p>
						}
						sections={[
							{
								heading: 'Imported vs Available',
								body: (
									<p>
										<strong>Imported</strong> APIs already live in your
										workspace. <strong>Available</strong> APIs can be imported
										to register them locally.
									</p>
								),
							},
							{
								heading: 'Previewing operations',
								body: (
									<p>
										Open any API to preview its operations before importing — no
										registration required.
									</p>
								),
							},
						]}
					/>
				}
			/>

			<DiscoverToolbar
				query={query}
				onQueryChange={setQuery}
				filter={filter}
				onFilterChange={setFilter}
				onRefresh={refresh}
				loading={catalog.isFetching}
				refreshing={isRefreshing}
			/>

			<div className="mt-4 space-y-4">
				<DiscoverStatusRow
					catalogTotal={catalog.catalogTotal}
					registeredCount={catalog.registeredCount}
					manifestAgeSeconds={catalog.manifestAgeSeconds}
					loading={catalog.isPending}
				/>

				<DiscoveryGrid
					entities={catalog.entities}
					loading={catalog.isPending}
					error={catalog.error}
					activeId={sheetOpen ? (selected?.id ?? null) : null}
					onOpen={handleOpen}
					onImport={importEntity}
					pendingApiIds={pendingApiIds}
					hasQuery={debouncedQuery.length > 0}
					hasNextPage={catalog.hasNextPage}
					isFetchingNextPage={catalog.isFetchingNextPage}
					onLoadMore={catalog.fetchNextPage}
				/>
			</div>

			<ApiDetailSheet
				entity={sheetEntity}
				open={sheetOpen}
				onClose={() => setSheetOpen(false)}
				onImport={importEntity}
				importPending={sheetEntity != null && pendingApiIds.has(sheetEntity.apiId)}
			/>
		</PageShell>
	);
}
