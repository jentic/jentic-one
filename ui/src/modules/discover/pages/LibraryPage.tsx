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
 * The catalog is a ledger (`CatalogLedger`): vendors grouped A–Z with a jump
 * rail while browsing, a flat ranked list while searching; drag a row onto
 * the docked panel (or use its Add button) to add it.
 *
 * Imported catalog rows link straight to the API's hub when the registry maps
 * the entry (`catalog_api_id`) to exactly one workspace API.
 *
 * Layering: this view reaches the backend only through the module's own
 * `api/hooks` (useDiscoverCatalog / useImportCatalogApi / useWorkspaceDigest…).
 * The digest reads the registry through shared hooks — never the workspace
 * module (sibling-import boundary).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Upload } from 'lucide-react';
import {
	PageShell,
	PageHeader,
	PageHelp,
	Button,
	ResizeHandle,
	useReportRightDock,
} from '@/shared/ui';
import { shellScroller, titleFromApiId } from '@/shared/lib';
import { ImportSpecDialog } from '@/shared/credentials/components/ImportSpecDialog';
import { useAllCredentials } from '@/shared/credentials/api';
import { DiscoverToolbar } from '@/modules/discover/components/DiscoverToolbar';
import { CatalogLedger } from '@/modules/discover/components/CatalogLedger';
import { useDragToAdd } from '@/modules/discover/lib/useDragToAdd';
import { workspaceHrefFor } from '@/modules/discover/lib/catalogRelations';
import { ApiDetailSheet } from '@/modules/discover/components/ApiDetailSheet';
import { DiscoverStatusRow } from '@/modules/discover/components/DiscoverStatusRow';
import {
	WorkspaceDockPanel,
	type PendingImport,
} from '@/modules/discover/components/WorkspaceDockPanel';
import { WorkspaceSummaryBar } from '@/modules/discover/components/WorkspaceSummaryBar';
import { usePanelCredentialFlow } from '@/modules/discover/components/usePanelCredentialFlow';
import {
	useConsumedFlagParam,
	useDebouncedValue,
	useMediaQuery,
	useResizableWidth,
} from '@/shared/hooks';
import { useFitToViewport } from '@/modules/discover/lib/useFitToViewport';
import { useJustLanded } from '@/modules/discover/lib/useJustLanded';
import {
	frontierKeyOf,
	jumpStartKey,
	previousJumpStartKey,
	vendorOf,
	type RailLetter,
} from '@/modules/discover/lib/catalogGroups';
import {
	useCatalogInWorkspace,
	useCatalogJump,
	useDiscoverCatalog,
	useImportCatalogApi,
	useRefreshCatalog,
	useWorkspaceDigest,
	type CatalogFilter,
	type DiscoveryEntity,
} from '@/modules/discover/api';

const XL_QUERY = '(min-width: 1280px)';
/** The resizer's grid column (it doubles as the catalog ↔ panel gap). */
const HANDLE_PX = 24;
/** The docked panel never gets narrower than this… */
const DOCK_MIN_PX = 320;
/** …and the catalog never narrower than this (nor the panel past half the area). */
const CATALOG_MIN_PX = 480;
const dockMaxFor = (gridWidth: number) =>
	Math.min(gridWidth * 0.5, gridWidth - HANDLE_PX - CATALOG_MIN_PX);

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
	// Browsing everything: the "In your workspace" group lists all of them.
	const browsingAll = debouncedQuery.length === 0 && filter === 'all';
	// Fired only once the main feed has settled: on a fresh install concurrent
	// first reads race to build the catalog snapshot, and one of them loses.
	const workspaceEntities = useCatalogInWorkspace({
		enabled: browsingAll && !catalog.isPending && !catalog.error,
		pollWhilePending: hasPendingImports,
	});
	// A–Z rail jump to a letter not loaded yet: a second keyset range that
	// starts at the letter (one page instead of paging through the rest).
	// Dropped whenever the query or filter changes.
	const [jumpStart, setJumpStart] = useState<{ key: string; scope: string } | null>(null);
	const jumpScope = `${debouncedQuery}\u0000${filter}`;
	const activeJumpKey = jumpStart?.scope === jumpScope ? jumpStart.key : null;
	const jumpFeed = useCatalogJump({
		startKey: debouncedQuery.length === 0 ? activeJumpKey : null,
		filter,
		pollWhilePending: hasPendingImports,
	});
	const handleJump = useCallback(
		(letter: RailLetter) => setJumpStart({ key: jumpStartKey(letter), scope: jumpScope }),
		[jumpScope],
	);
	const { startKey: jumpRangeStart, loadEarlierFrom } = jumpFeed;
	// Back one letter — or only back to where the head has loaded, so the
	// two ranges meet (and merge) without refetching the head's rows.
	const headFrontier = useMemo(() => frontierKeyOf(catalog.entities), [catalog.entities]);
	const loadEarlier = useCallback(() => {
		const prev = jumpRangeStart != null ? previousJumpStartKey(jumpRangeStart) : null;
		if (prev) loadEarlierFrom(headFrontier > prev ? headFrontier : prev);
	}, [jumpRangeStart, loadEarlierFrom, headFrontier]);
	const ledgerJump = useMemo(
		() =>
			jumpRangeStart != null && debouncedQuery.length === 0
				? {
						startKey: jumpRangeStart,
						entities: jumpFeed.entities,
						hasNextPage: jumpFeed.hasNextPage,
						isFetchingNextPage: jumpFeed.isFetchingNextPage,
						isPending: jumpFeed.isPending,
						isFetched: jumpFeed.isFetched,
						error: jumpFeed.error,
						onLoadMore: jumpFeed.fetchNextPage,
						onLoadEarlier: loadEarlier,
						isLoadingEarlier: jumpFeed.isLoadingEarlier,
					}
				: undefined,
		[
			jumpRangeStart,
			loadEarlier,
			jumpFeed.isLoadingEarlier,
			debouncedQuery,
			jumpFeed.entities,
			jumpFeed.hasNextPage,
			jumpFeed.isFetchingNextPage,
			jumpFeed.isPending,
			jumpFeed.isFetched,
			jumpFeed.error,
			jumpFeed.fetchNextPage,
		],
	);
	const { refresh, isRefreshing } = useRefreshCatalog();
	const digest = useWorkspaceDigest();
	// Toasts sit left of the docked workspace panel (the grid's last column at xl).
	const dockGridRef = useRef<HTMLDivElement>(null);
	useReportRightDock(dockGridRef, { lastChild: true });
	// The panel's "no credential" names open the shared Add credential
	// flow here, over the catalog, on that API's form.
	const credentialFlow = usePanelCredentialFlow();
	// The same drained list (and cache slice) the digest's health index reads;
	// the rows' "Credential ready" matches not-yet-imported entries against it.
	const allCredentials = useAllCredentials();
	const credentials = allCredentials.complete ? allCredentials.items : null;
	// Tailwind `xl` — where the grid below goes two-column and docks the panel.
	const isXl = useMediaQuery(XL_QUERY);
	const dockRef = useRef<HTMLElement>(null);
	const catalogColRef = useRef<HTMLDivElement>(null);
	const handleColRef = useRef<HTMLDivElement>(null);
	// The dock and its resizer both end at the viewport's bottom — or with the
	// list, at the end of the page (so neither is pushed up under the top bar).
	useFitToViewport(dockRef, { enabled: isXl, until: catalogColRef });
	useFitToViewport(handleColRef, { enabled: isXl, until: catalogColRef });
	// Drag the split between the catalog and the docked panel; persisted.
	const dockWidth = useResizableWidth({
		storageKey: 'library.workspaceWidth',
		containerRef: dockGridRef,
		panelRef: dockRef,
		cssVar: '--ws-dock-w',
		min: DOCK_MIN_PX,
		maxFor: dockMaxFor,
		enabled: isXl,
	});

	// Every catalog row currently loaded: the head feed, a rail jump's range,
	// and the separately-fetched "In your workspace" group (browsing only).
	// An import can start from any of them, so its landing is looked up in all.
	const loadedEntities = useMemo(
		() => [
			...catalog.entities,
			...jumpFeed.entities,
			...(browsingAll ? workspaceEntities : []),
		],
		[catalog.entities, jumpFeed.entities, workspaceEntities, browsingAll],
	);

	// The count line's vendor figure: distinct vendors among the rows loaded so
	// far (the catalog response carries no vendor total). Browsing only — a
	// search or filter loads a subset, which says nothing about the catalog.
	const vendorsLoaded = useMemo(() => {
		if (!browsingAll) return 0;
		return new Set(loadedEntities.map((e) => vendorOf(e).toLowerCase())).size;
	}, [browsingAll, loadedEntities]);
	const vendorsComplete = browsingAll && !catalog.hasNextPage && !catalog.isPending;

	// When the (polled) feeds update, resolve any pending import whose entry has
	// flipped to registered — clears the row's "Adding…" state + toasts.
	useEffect(() => {
		reconcileImported(loadedEntities);
	}, [loadedEntities, reconcileImported]);

	// Imports that just landed get a brief "just added" flash in the panel.
	const justAdded = useJustLanded(pendingApiIds);

	// Drag a catalog row onto the docked panel — only where the panel is docked.
	const drag = useDragToAdd({ enabled: isXl, onDrop: importEntity });

	// The catalog scrolls on the shell's scroller (no bounded results
	// container), so a new query or filter re-ranks the list but leaves the
	// viewport wherever the user last scrolled — burying the freshly-ranked top
	// matches off-screen (#602).
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
	const sheetEntity = (selected && loadedEntities.find((e) => e.id === selected.id)) ?? selected;
	const sheetWorkspaceHref = workspaceHrefFor(
		sheetEntity ? digest.byCatalogApiId.get(sheetEntity.apiId) : undefined,
	);

	// The panel's "Adding…" rows: the catalog's in-flight imports, labelled
	// the same way the catalog rows title them.
	const pendingImports = useMemo<PendingImport[]>(
		() =>
			[...pendingApiIds].map((apiId) => ({
				apiId,
				label:
					loadedEntities.find((e) => e.apiId === apiId)?.summary ?? titleFromApiId(apiId),
			})),
		[pendingApiIds, loadedEntities],
	);

	return (
		<PageShell spacing="space-y-3">
			<PageHeader
				title="Library"
				subtitle="Everything your agents can use. Browse the public catalog, and keep an eye on the APIs in your workspace."
				actions={
					<>
						<Button
							variant="primary"
							size="sm"
							className="rounded-field h-8 px-3 text-[13px] font-semibold"
							onClick={openImportOwn}
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
											Browse the public catalog by vendor (jump with the A–Z
											rail) or search it, and add an API with{' '}
											<strong>Add</strong> — or drag its row onto{' '}
											<strong>Your workspace</strong>. Rows marked{' '}
											<strong>In your workspace</strong> are already added;{' '}
											<strong>Open</strong> takes you to that API's page.
										</p>
									),
								},
								{
									heading: 'Previewing operations',
									body: (
										<p>
											Click any row to preview its operations before you add
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
				// The panel widens with the viewport (24 → 28 → 30rem) by default so
				// the full, filterable list has room. The width is a custom property
				// so the arbitrary breakpoints can't be out-ordered by `xl:` — and so
				// the resizer (`useResizableWidth`) can override it inline. The
				// middle 24px column is the resizer, standing in for the gap.
				className="grid grid-cols-1 items-start gap-6 [--ws-dock-w:24rem] min-[1440px]:[--ws-dock-w:28rem] min-[1800px]:[--ws-dock-w:30rem] xl:grid-cols-[minmax(0,1fr)_24px_var(--ws-dock-w)] xl:gap-x-0"
			>
				{/* The catalog's counts head its column, and the sticky toolbar's
				    own padding spaces them from the search — no full-width strip
				    (and gap) of their own under the header. */}
				<div ref={catalogColRef} className="min-w-0">
					{/* While the catalog is in error its counts are zeros, not facts. */}
					{!catalog.error && (
						<DiscoverStatusRow
							catalogTotal={catalog.catalogTotal}
							registeredCount={catalog.registeredCount}
							outdatedCount={catalog.outdatedCount}
							manifestAgeSeconds={catalog.manifestAgeSeconds}
							loading={catalog.isPending}
							vendorsLoaded={vendorsLoaded}
							vendorsComplete={vendorsComplete}
						/>
					)}

					<DiscoverToolbar
						query={query}
						onQueryChange={setQuery}
						filter={filter}
						onFilterChange={setFilter}
						onRefresh={refresh}
						loading={catalog.isFetching}
						refreshing={isRefreshing}
					/>

					<CatalogLedger
						entities={catalog.entities}
						loading={catalog.isPending}
						error={catalog.error}
						onRetry={catalog.refetch}
						retrying={catalog.isFetching}
						activeId={sheetOpen ? (selected?.id ?? null) : null}
						onOpen={handleOpen}
						onImport={importEntity}
						pendingApiIds={pendingApiIds}
						query={debouncedQuery}
						hasNextPage={catalog.hasNextPage}
						isFetchingNextPage={catalog.isFetchingNextPage}
						onLoadMore={catalog.fetchNextPage}
						workspaceByCatalogId={digest.byCatalogApiId}
						credentials={credentials}
						onImportOwn={openImportOwn}
						drag={isXl ? drag : undefined}
						announcement={drag.announcement}
						workspaceEntities={browsingAll ? workspaceEntities : undefined}
						jump={ledgerJump}
						onJump={handleJump}
					/>
					{drag.ghostElement}
				</div>

				{isXl && (
					<div
						ref={handleColRef}
						className="sticky top-4 h-[calc(100dvh-5rem)]"
						data-testid="workspace-resize-column"
					>
						<ResizeHandle
							label="Resize workspace panel"
							value={dockWidth.width}
							min={dockWidth.min}
							max={dockWidth.max}
							onPreview={dockWidth.preview}
							onCommit={dockWidth.commit}
							onReset={dockWidth.reset}
							className="h-full w-full"
							data-testid="workspace-resize-handle"
						/>
					</div>
				)}
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
						drop={drag.drop}
						justAddedApiIds={justAdded}
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
