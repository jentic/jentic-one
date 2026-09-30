/**
 * Library — the Catalog view (default landing, `/library`).
 *
 * One nav area for everything you can give agents to use: APIs, in two views —
 *
 *   - the public **Catalog** (this page): browse/search the Jentic catalog
 *     (`GET /catalog`, keyset-paged), preview an entry's operations in a sheet,
 *     and import it (`POST /catalog/{id}:import`, async);
 *   - **Your workspace**: docked here on the right as a live digest (what needs
 *     you, what's importing, what you have, what just changed), and one click
 *     (Expand / "Open your workspace") away from the full Workspace view
 *     (`/library/workspace`, owned by the workspace module) — the two morph via
 *     a shared view transition. Below `xl` (one column) the panel isn't docked
 *     under the endless catalog; a summary bar above it opens the same content
 *     in a bottom sheet (`WorkspaceSummaryBar`).
 *
 * Imported catalog cards link straight to the API's hub when the registry maps
 * the entry (`catalog_api_id`) to exactly one workspace API.
 *
 * Layering: this view reaches the backend only through the module's own
 * `api/hooks` (useDiscoverCatalog / useImportCatalogApi / useWorkspaceDigest…).
 * The digest reads the registry through shared hooks — never the workspace
 * module (sibling-import boundary).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Upload } from 'lucide-react';
import { PageShell, PageHeader, PageHelp, Button, useReportRightDock } from '@/shared/ui';
import { shellScroller, titleFromApiId } from '@/shared/lib';
import { ImportSpecDialog } from '@/shared/credentials/components/ImportSpecDialog';
import { useAllCredentials } from '@/shared/credentials/api';
import { DiscoverToolbar } from '@/modules/discover/components/DiscoverToolbar';
import { DiscoveryGrid } from '@/modules/discover/components/DiscoveryGrid';
import { workspaceHrefFor } from '@/modules/discover/lib/catalogRelations';
import { ApiDetailSheet } from '@/modules/discover/components/ApiDetailSheet';
import { DiscoverStatusRow } from '@/modules/discover/components/DiscoverStatusRow';
import {
	WorkspaceDockPanel,
	type PendingImport,
} from '@/modules/discover/components/WorkspaceDockPanel';
import { WorkspaceSummaryBar } from '@/modules/discover/components/WorkspaceSummaryBar';
import { useConsumedFlagParam, useMediaQuery } from '@/shared/hooks';
import { useDebouncedValue } from '@/modules/discover/lib/useDebouncedValue';
import {
	useDiscoverCatalog,
	useImportCatalogApi,
	useRefreshCatalog,
	useWorkspaceDigest,
	type CatalogFilter,
	type DiscoveryEntity,
} from '@/modules/discover/api';

const XL_QUERY = '(min-width: 1280px)';

export default function LibraryPage() {
	const [query, setQuery] = useState('');
	const [filter, setFilter] = useState<CatalogFilter>('all');
	const [selected, setSelected] = useState<DiscoveryEntity | null>(null);
	const [sheetOpen, setSheetOpen] = useState(false);
	// `?import=1` deep link: opens the own-spec import dialog on arrival.
	const [importOwnOpen, setImportOwnOpen] = useConsumedFlagParam('import');

	const debouncedQuery = useDebouncedValue(query, 250);
	const { importEntity, pendingApiIds, hasPendingImports, reconcileImported } =
		useImportCatalogApi();
	const catalog = useDiscoverCatalog({
		q: debouncedQuery,
		filter,
		pollWhilePending: hasPendingImports,
	});
	const { refresh, isRefreshing } = useRefreshCatalog();
	const digest = useWorkspaceDigest();
	// Toasts sit left of the docked workspace panel (the grid's last column at xl).
	const dockGridRef = useRef<HTMLDivElement>(null);
	useReportRightDock(dockGridRef, { lastChild: true });
	// The same drained list (and cache slice) the digest's health index reads;
	// the tiles' "Credential ready" matches not-yet-imported entries against it.
	const allCredentials = useAllCredentials();
	const credentials = allCredentials.complete ? allCredentials.items : null;
	// Tailwind `xl` — where the grid below goes two-column and docks the panel.
	const isXl = useMediaQuery(XL_QUERY);

	// When the (polled) feed updates, resolve any pending import whose entry has
	// flipped to registered — clears the card's "Adding…" state + toasts.
	useEffect(() => {
		reconcileImported(catalog.entities);
	}, [catalog.entities, reconcileImported]);

	// The catalog scrolls on the window (no bounded results container), so a new
	// query or filter re-ranks the list but leaves the viewport wherever the user
	// last scrolled — burying the freshly-ranked top matches off-screen (#602).
	// Snap back to the top whenever the committed query or filter actually
	// changes. Comparing the previous values (rather than a "have I mounted"
	// boolean) is StrictMode-safe.
	const prevQueryRef = useRef(debouncedQuery);
	const prevFilterRef = useRef(filter);
	useEffect(() => {
		if (prevQueryRef.current === debouncedQuery && prevFilterRef.current === filter) {
			return;
		}
		prevQueryRef.current = debouncedQuery;
		prevFilterRef.current = filter;
		shellScroller().scrollTo({ top: 0, left: 0 });
	}, [debouncedQuery, filter]);

	const handleOpen = useCallback((entity: DiscoveryEntity) => {
		setSelected(entity);
		setSheetOpen(true);
	}, []);
	const openImportOwn = useCallback(() => setImportOwnOpen(true), [setImportOwnOpen]);

	// Prefer the live catalog row for the open sheet so its footer reflects a
	// poll-driven Available → In your workspace flip; fall back to the opened snapshot.
	const sheetEntity =
		(selected && catalog.entities.find((e) => e.id === selected.id)) ?? selected;
	const sheetWorkspaceHref = workspaceHrefFor(
		sheetEntity ? digest.byCatalogApiId.get(sheetEntity.apiId) : undefined,
	);

	// The panel's "Adding…" rows: the catalog's in-flight imports, labelled
	// the same way the catalog card titles them.
	const pendingImports = useMemo<PendingImport[]>(
		() =>
			[...pendingApiIds].map((apiId) => ({
				apiId,
				label:
					catalog.entities.find((e) => e.apiId === apiId)?.summary ??
					titleFromApiId(apiId),
			})),
		[pendingApiIds, catalog.entities],
	);

	return (
		<PageShell spacing="space-y-3">
			<PageHeader
				title="Library"
				subtitle="Everything your agents can use. Browse the public catalog, and keep an eye on the APIs in your workspace."
				actions={
					<>
						<Button
							variant="outline"
							size="sm"
							onClick={() => setImportOwnOpen(true)}
							data-testid="discover-upload-own"
						>
							<Upload size={14} aria-hidden="true" />
							Import your own API
						</Button>
						<PageHelp
							title="About the Library"
							intro={
								<p>
									The Library holds what your agents can call. Today that's APIs:
									the public Jentic catalog on the left, and your workspace — the
									APIs imported into this instance — docked on the right.
								</p>
							}
							sections={[
								{
									heading: 'Catalog vs your workspace',
									body: (
										<p>
											Catalog entries marked{' '}
											<strong>In your workspace</strong> are already added;{' '}
											<strong>Available</strong> ones can be added to your
											workspace with <strong>Add to workspace</strong>. Click
											any card to open its preview. On an imported card,{' '}
											<strong>Open</strong> goes to the API's hub when exactly
											one workspace API matches it, otherwise to your
											workspace list.
										</p>
									),
								},
								{
									heading: 'Previewing operations',
									body: (
										<p>
											Open any API to preview its operations before adding it
											— no registration required.
										</p>
									),
								},
								{
									heading: 'The workspace panel',
									body: (
										<p>
											It lists only what needs you (updates, overlays awaiting
											review, missing credentials, failing calls, drafts),
											what's being added, your APIs, and recent API events.
											Expand it for the full workspace.
										</p>
									),
								},
							]}
						/>
					</>
				}
			/>

			{/* Below xl the docked panel would sit under an infinite catalog —
			    unreachable — so a summary bar above the catalog opens the same
			    panel content in a bottom sheet instead. */}
			{!isXl && (
				<WorkspaceSummaryBar
					digest={digest}
					pendingImports={pendingImports}
					onImportOwn={openImportOwn}
				/>
			)}

			<div
				ref={dockGridRef}
				className="grid grid-cols-1 items-start gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(320px,24rem)]"
			>
				{/* The catalog's counts head its column, and the sticky toolbar's
				    own padding spaces them from the search — no full-width strip
				    (and gap) of their own under the header. */}
				<div className="min-w-0">
					<DiscoverStatusRow
						catalogTotal={catalog.catalogTotal}
						registeredCount={catalog.registeredCount}
						outdatedCount={catalog.outdatedCount}
						manifestAgeSeconds={catalog.manifestAgeSeconds}
						loading={catalog.isPending}
						workspace={digest.complete ? digest.totals : null}
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

					<div className="mt-3">
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
							workspaceByCatalogId={digest.byCatalogApiId}
							credentialsError={digest.credentialsError}
							credentials={credentials}
							onImportOwn={openImportOwn}
						/>
					</div>
				</div>

				{isXl && (
					<WorkspaceDockPanel
						// Sticks 1rem under the 3rem top bar and stops 1rem above the
						// viewport's bottom edge; the body scrolls inside.
						className="xl:sticky xl:top-4 xl:h-[calc(100dvh-5rem)]"
						digest={digest}
						pendingImports={pendingImports}
						onImportOwn={openImportOwn}
					/>
				)}
			</div>

			<ApiDetailSheet
				entity={sheetEntity}
				open={sheetOpen}
				onClose={() => setSheetOpen(false)}
				onImport={importEntity}
				importPending={sheetEntity != null && pendingApiIds.has(sheetEntity.apiId)}
				workspaceHref={sheetWorkspaceHref}
			/>

			<ImportSpecDialog open={importOwnOpen} onClose={() => setImportOwnOpen(false)} />
		</PageShell>
	);
}
