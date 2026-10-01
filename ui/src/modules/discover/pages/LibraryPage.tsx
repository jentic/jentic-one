/**
 * Library — the Catalog view (default landing, `/library`).
 *
 * One nav area for everything you can give agents to use: APIs, in two views —
 *
 *   - the public **Catalog** (this page): browse/search the Jentic catalog
 *     (`GET /catalog`, keyset-paged), preview an entry's operations in a sheet,
 *     and import it (`POST /catalog/{id}:import`, async);
 *   - **Your workspace**: docked here on the right — what needs you, what's
 *     importing, every API you have (filterable by text and serving state,
 *     `?q=` / `?status=`), and what just changed. It is the whole workspace
 *     view: the retired `/library/workspace` page redirects here, and each
 *     row opens the API's hub (`/library/workspace/:vendor/:name/:version`).
 *     Below `xl` (one column) the panel isn't docked under the endless
 *     catalog; a summary bar above it opens the same content in a bottom
 *     sheet (`WorkspaceSummaryBar`).
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
import { usePanelCredentialFlow } from '@/modules/discover/components/usePanelCredentialFlow';
import { useConsumedFlagParam, useMediaQuery } from '@/shared/hooks';
import { useDebouncedValue } from '@/modules/discover/lib/useDebouncedValue';
import { useFitToViewport } from '@/modules/discover/lib/useFitToViewport';
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
	// The panel's "no credential" names open the shared Add credential
	// flow here, over the catalog, on that API's form.
	const credentialFlow = usePanelCredentialFlow();
	// The same drained list (and cache slice) the digest's health index reads;
	// the tiles' "Credential ready" matches not-yet-imported entries against it.
	const allCredentials = useAllCredentials();
	const credentials = allCredentials.complete ? allCredentials.items : null;
	// Tailwind `xl` — where the grid below goes two-column and docks the panel.
	const isXl = useMediaQuery(XL_QUERY);
	const dockRef = useRef<HTMLElement>(null);
	useFitToViewport(dockRef, { enabled: isXl });

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
									The Library holds what your agents can call — today, APIs.
									Browse the public Jentic catalog; the APIs you've added live in{' '}
									<strong>Your workspace</strong>, alongside it.
								</p>
							}
							sections={[
								{
									heading: 'The catalog',
									body: (
										<p>
											Search the public catalog and add an API with{' '}
											<strong>Add to workspace</strong>. Cards marked{' '}
											<strong>In your workspace</strong> are already added;{' '}
											<strong>Open</strong> takes you to that API's page.
										</p>
									),
								},
								{
									heading: 'Previewing operations',
									body: (
										<p>
											Click any card to preview its operations before you add
											it — nothing is registered until you do.
										</p>
									),
								},
								{
									heading: 'Your workspace',
									body: (
										<p>
											Shows what needs your attention, every API you've added
											(filter by name or by Live / Draft / Update available),
											and recent changes. Click an API to open its page, or
											use <strong>Import your own API</strong> to add an
											OpenAPI spec.
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
					onAddCredential={credentialFlow.addCredentialFor}
					credentialNotice={credentialFlow.notice}
					onDismissCredentialNotice={credentialFlow.dismissNotice}
				/>
			)}

			<div
				ref={dockGridRef}
				// The panel widens with the viewport (24 → 28 → 30rem) so the full,
				// filterable list has room, while the catalog keeps its xl 2 /
				// 2xl 3 columns at a usable card width (1280 stays as before). The
				// width is a custom property so the arbitrary breakpoints can't be
				// out-ordered by `xl:`.
				className="grid grid-cols-1 items-start gap-6 [--ws-dock-w:24rem] min-[1440px]:[--ws-dock-w:28rem] min-[1800px]:[--ws-dock-w:30rem] xl:grid-cols-[minmax(0,1fr)_var(--ws-dock-w)]"
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
						ref={dockRef}
						// Sticks 1rem under the 3rem top bar and stops 1rem above the
						// viewport's bottom edge (`useFitToViewport` keeps that true
						// before it has stuck, too); the body scrolls inside.
						className="xl:sticky xl:top-4 xl:h-[calc(100dvh-5rem)]"
						digest={digest}
						pendingImports={pendingImports}
						onImportOwn={openImportOwn}
						onAddCredential={credentialFlow.addCredentialFor}
						credentialNotice={credentialFlow.notice}
						onDismissCredentialNotice={credentialFlow.dismissNotice}
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
			{credentialFlow.element}
		</PageShell>
	);
}
