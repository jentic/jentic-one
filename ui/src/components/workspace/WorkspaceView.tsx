import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import { WorkspaceCatalogFooter } from './WorkspaceCatalogFooter';
import { WorkspaceSearch, type WorkspaceSearchHandle } from './WorkspaceSearch';
import { WorkspaceStatsStrip } from './WorkspaceStatsStrip';
import { WorkspaceTile, type WorkspaceTileEntity } from './WorkspaceTile';
import type { ImportTab } from './ImportSourceDialog';
import { api } from '@/api/client';
import { Button } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { ToolkitCard, type ToolkitCardData } from '@/components/toolkits/ToolkitCard';
import { ToolkitDetailSheet } from '@/components/toolkits/ToolkitDetailSheet';
import { useToolkitDetailSheet } from '@/hooks/useToolkitDetailSheet';
import { useToolkitCardEnrichment } from '@/hooks/useToolkitCardEnrichment';
import { useRovingGridFocus } from '@/hooks/useRovingGridFocus';
import { isTypingTarget } from '@/lib/keyboard';
import { subscribeCredentialImported } from '@/lib/events/credentialImported';
import { toast } from '@/components/ui/toastStore';

/**
 * Workspace home surface.
 *
 * Owns its composition end-to-end — intentionally NOT a configuration of
 * `<DiscoveryView>`. The two pages serve different jobs (Workspace = your
 * stuff at-a-glance; Discover = full catalog browser) and reusing one
 * component for both kept collapsing them into a single visual.
 *
 * Layout:
 *
 *   <WorkspaceStatsStrip />            // dashboard ribbon
 *   <WorkspaceSearch />                // in-memory filter, no BM25
 *   <section "APIs">                   // grid of WorkspaceTile (api)
 *   <section "Workflows">              // grid of WorkspaceTile (workflow); omitted if zero
 *   <WorkspaceCatalogFooter />         // single-line CTA to /discover
 *
 * Navigation:
 *  - API tiles navigate to `/workspace/apis/:apiId` (full detail page).
 *  - Workflow tiles navigate to `/workspace/workflows/:slug`.
 */

const WORKSPACE_PAGE_SIZE = 60;

export function WorkspaceView({
	onRequestImport,
}: {
	/**
	 * Called when an inline empty-state CTA is clicked. The page mounts
	 * the import dialog, so it owns the open state — the view just
	 * forwards the user intent. Optional so existing callers (and tests
	 * that don't care about the import flow) keep working unchanged.
	 */
	onRequestImport?: (tab: ImportTab) => void;
} = {}) {
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const toolkitSheet = useToolkitDetailSheet();

	// ── Filter input ──────────────────────────────────────────────────────
	//
	// Declared before the data block because the API list now fans out as
	// `/apis?q=…&page=…&source=local` so the workspace can find rows that
	// aren't on page 1 (workspaces with >60 APIs). The text input feeds a
	// debounced server query; the workflow filter still runs in-memory
	// because workflows aren't paginated.
	const [filterInput, setFilterInput] = useState('');
	const trimmedFilter = filterInput.trim();
	const [debouncedFilter, setDebouncedFilter] = useState('');
	useEffect(() => {
		const handle = window.setTimeout(() => setDebouncedFilter(trimmedFilter), 200);
		return () => window.clearTimeout(handle);
	}, [trimmedFilter]);
	const searchRef = useRef<WorkspaceSearchHandle>(null);
	const apisGridRef = useRef<HTMLDivElement>(null);
	const workflowsGridRef = useRef<HTMLDivElement>(null);
	const onApisKeyDown = useRovingGridFocus(
		apisGridRef,
		'button[data-testid="workspace-tile-api"]',
	);
	const onWorkflowsKeyDown = useRovingGridFocus(
		workflowsGridRef,
		'button[data-testid="workspace-tile-workflow"]',
	);

	// ── Workspace data ────────────────────────────────────────────────────
	//
	// Paginated APIs probe. Each page accumulates into a single list so the
	// grid renders progressively when the user clicks "Load more". The
	// query key includes the debounced filter so typing in the search box
	// pivots to a server-filtered listing — without that, a workspace with
	// >`WORKSPACE_PAGE_SIZE` APIs would silently hide rows past the first
	// page (the bug that motivated this fan-out: importing Slack and not
	// finding it because alphabetically it landed at row 65).
	const [apisPage, setApisPage] = useState(1);
	const [apisAccumulator, setApisAccumulator] = useState<Array<Record<string, unknown>>>([]);

	// Reset paging + accumulator when the filter changes. We key off the
	// *debounced* value so a single keystroke doesn't blow the accumulator
	// away mid-typing (which would briefly show "no results" before the
	// next-page response arrives).
	const apisFilterRef = useRef(debouncedFilter);
	useEffect(() => {
		if (apisFilterRef.current === debouncedFilter) return;
		apisFilterRef.current = debouncedFilter;
		setApisPage(1);
		setApisAccumulator([]);
	}, [debouncedFilter]);

	const apisQuery = useQuery({
		queryKey: ['workspace', 'apis', apisPage, WORKSPACE_PAGE_SIZE, debouncedFilter || null],
		queryFn: () =>
			api.listApis(apisPage, WORKSPACE_PAGE_SIZE, 'local', debouncedFilter || undefined),
		staleTime: 30_000,
		placeholderData: (prev) => prev,
	});
	const apisPayload = apisQuery.data as
		| { data?: Array<Record<string, unknown>>; total?: number; total_pages?: number }
		| undefined;

	// Merge each page into the accumulator, deduping by `id` so a refetch
	// of page 1 (e.g. after `credentialImported`) updates rows in place
	// without duplicating them when later pages have already loaded.
	useEffect(() => {
		if (apisQuery.isPlaceholderData) return;
		const incoming = apisPayload?.data;
		if (!Array.isArray(incoming)) return;
		if (apisPage === 1) {
			setApisAccumulator(incoming);
			return;
		}
		setApisAccumulator((prev) => {
			const indexById = new Map<string, number>();
			prev.forEach((row, idx) => {
				const id = typeof row?.id === 'string' ? row.id : '';
				if (id) indexById.set(id, idx);
			});
			const next = prev.slice();
			for (const row of incoming) {
				const id = typeof row?.id === 'string' ? row.id : '';
				const existingIdx = id ? indexById.get(id) : undefined;
				if (existingIdx !== undefined) {
					next[existingIdx] = row;
				} else {
					if (id) indexById.set(id, next.length);
					next.push(row);
				}
			}
			return next;
		});
	}, [apisPayload, apisPage, apisQuery.isPlaceholderData]);

	const apis = apisAccumulator;
	const apisTotal = apisPayload?.total ?? apis.length;
	const apisTotalPages = apisPayload?.total_pages ?? 1;
	const apisHasMore = apisPage < apisTotalPages;
	const apisIsFetchingMore = apisQuery.isFetching && apisPage > 1;

	// Paginated workflows probe — same shape and rationale as the APIs
	// fan-out above. `GET /workflows` returns a bare list when no `page`
	// / `limit` is supplied (preserving every other consumer), and an
	// envelope when we ask for a page. Without this, a workspace with
	// >`WORKSPACE_PAGE_SIZE` workflows would silently truncate the grid.
	const [workflowsPage, setWorkflowsPage] = useState(1);
	const [workflowsAccumulator, setWorkflowsAccumulator] = useState<
		Array<Record<string, unknown>>
	>([]);
	const workflowsFilterRef = useRef(debouncedFilter);
	useEffect(() => {
		if (workflowsFilterRef.current === debouncedFilter) return;
		workflowsFilterRef.current = debouncedFilter;
		setWorkflowsPage(1);
		setWorkflowsAccumulator([]);
	}, [debouncedFilter]);

	const workflowsQuery = useQuery({
		queryKey: [
			'workspace',
			'workflows',
			workflowsPage,
			WORKSPACE_PAGE_SIZE,
			debouncedFilter || null,
		],
		queryFn: () =>
			api.listWorkflowsPaged(
				workflowsPage,
				WORKSPACE_PAGE_SIZE,
				'local',
				debouncedFilter || undefined,
			),
		staleTime: 60_000,
		placeholderData: (prev) => prev,
	});
	const workflowsPayload = workflowsQuery.data as
		| { data?: Array<Record<string, unknown>>; total?: number; total_pages?: number }
		| undefined;

	useEffect(() => {
		if (workflowsQuery.isPlaceholderData) return;
		const incoming = workflowsPayload?.data;
		if (!Array.isArray(incoming)) return;
		if (workflowsPage === 1) {
			setWorkflowsAccumulator(incoming);
			return;
		}
		setWorkflowsAccumulator((prev) => {
			const indexBySlug = new Map<string, number>();
			prev.forEach((row, idx) => {
				const slug = typeof row?.slug === 'string' ? (row.slug as string) : '';
				if (slug) indexBySlug.set(slug, idx);
			});
			const next = prev.slice();
			for (const row of incoming) {
				const slug = typeof row?.slug === 'string' ? (row.slug as string) : '';
				const existingIdx = slug ? indexBySlug.get(slug) : undefined;
				if (existingIdx !== undefined) {
					next[existingIdx] = row;
				} else {
					if (slug) indexBySlug.set(slug, next.length);
					next.push(row);
				}
			}
			return next;
		});
	}, [workflowsPayload, workflowsPage, workflowsQuery.isPlaceholderData]);

	const workflows = workflowsAccumulator;
	const workflowsTotal = workflowsPayload?.total ?? workflows.length;
	const workflowsTotalPages = workflowsPayload?.total_pages ?? 1;
	const workflowsHasMore = workflowsPage < workflowsTotalPages;
	const workflowsIsFetchingMore = workflowsQuery.isFetching && workflowsPage > 1;

	// ── Toolkit → API coverage map ───────────────────────────────────────
	//
	// Each toolkit "covers" the APIs whose credentials it has bound. The
	// public list endpoint (`/toolkits`) only returns counts, not the
	// per-toolkit credential→api_id mapping, so we fan out one
	// `/toolkits/:id/credentials` call per toolkit to build a
	// `Map<api_id, toolkitName[]>` once. With the typical Mini deployment
	// (a handful of toolkits) this is cheap and stays cached for 60s.
	const toolkitsQuery = useQuery({
		queryKey: ['workspace', 'toolkits'],
		queryFn: () => api.listToolkits(),
		staleTime: 60_000,
	});
	const toolkits = useMemo<Array<{ id: string; name: string }>>(() => {
		const data = toolkitsQuery.data as Array<{ id?: unknown; name?: unknown }> | undefined;
		return Array.isArray(data)
			? data.map((t) => ({
					id: String(t?.id ?? ''),
					name: String(t?.name ?? t?.id ?? ''),
				}))
			: [];
	}, [toolkitsQuery.data]);

	// Full card data for the Toolkits section. Reuses the SAME query as the
	// coverage map above (no extra network call) — we just keep the roll-up
	// fields (`disabled` / `simulate` / counts) that `ToolkitCard` reads.
	const toolkitCards = useMemo<ToolkitCardData[]>(() => {
		const data = toolkitsQuery.data as Array<Record<string, unknown>> | undefined;
		if (!Array.isArray(data)) return [];
		return data.map((t) => ({
			id: String(t?.id ?? ''),
			name: String(t?.name ?? t?.id ?? ''),
			description: typeof t?.description === 'string' ? t.description : null,
			created_at: typeof t?.created_at === 'number' ? t.created_at : null,
			simulate: Boolean(t?.simulate),
			disabled: Boolean(t?.disabled),
			key_count: typeof t?.key_count === 'number' ? t.key_count : undefined,
			credential_count:
				typeof t?.credential_count === 'number' ? t.credential_count : undefined,
		}));
	}, [toolkitsQuery.data]);

	const toolkitEnrichment = useToolkitCardEnrichment(toolkitCards.map((t) => t.id));

	// Reverse the per-toolkit enrichment (toolkit → apiIds) into the apiId →
	// toolkit-names map the API tiles need. Reusing the enrichment hook's
	// per-toolkit credential queries avoids the duplicate `listToolkitCredentials`
	// fan-out this view used to fire on its own.
	const apiToolkitMap = useMemo<Map<string, string[]>>(() => {
		const map = new Map<string, string[]>();
		for (const t of toolkits) {
			if (t.id === 'default') continue;
			const apiIds = toolkitEnrichment.get(t.id)?.apiIds ?? [];
			for (const apiId of apiIds) {
				const existing = map.get(apiId) ?? [];
				if (!existing.includes(t.name)) existing.push(t.name);
				map.set(apiId, existing);
			}
		}
		return map;
	}, [toolkits, toolkitEnrichment]);

	const hasDefaultToolkit = toolkits.some((t) => t.id === 'default');

	const filteredApis = useMemo<WorkspaceTileEntity[]>(() => {
		// APIs come from the server already filtered by the debounced
		// `q=`. We only do row → tile-entity shaping here.
		return apis.map((row) => {
			const id = String(row.id ?? '');
			const explicitToolkits = apiToolkitMap.get(id) ?? [];
			const hasCreds = Boolean(row.has_credentials);
			const toolkitCount = explicitToolkits.length + (hasDefaultToolkit && hasCreds ? 1 : 0);
			const toolkitNames = Array.from({ length: toolkitCount }, (_, i) =>
				i < explicitToolkits.length ? explicitToolkits[i] : 'default',
			);
			return {
				kind: 'api',
				id,
				name: String(row.name ?? row.id ?? ''),
				description:
					typeof row.description === 'string' ? (row.description as string) : undefined,
				hasCredentials: hasCreds,
				toolkitNames,
				operationCount:
					typeof row.operation_count === 'number' ? row.operation_count : undefined,
				credentialCount:
					typeof row.credential_count === 'number' ? row.credential_count : undefined,
				workflowCount:
					typeof row.workflow_count === 'number' ? row.workflow_count : undefined,
				importedAt: typeof row.created_at === 'number' ? row.created_at : undefined,
			};
		});
	}, [apis, apiToolkitMap, hasDefaultToolkit]);

	const filteredWorkflows = useMemo<WorkspaceTileEntity[]>(() => {
		// Workflows come from the server already filtered by the
		// debounced `q=` (same protocol as the APIs fan-out). We only
		// shape rows into tile entities here.
		return workflows.map((row) => {
			// `/workflows` returns `steps_count: number` and
			// `involved_apis: string[]`. The earlier draft of this
			// component read `row.steps` / `row.api_ids` — those keys
			// don't exist on the response and silently produced
			// "0 steps · 0 APIs" for every tile.
			const steps =
				typeof row.steps_count === 'number' ? (row.steps_count as number) : undefined;
			const involved = Array.isArray(row.involved_apis)
				? (row.involved_apis as unknown[]).map(String)
				: [];
			return {
				kind: 'workflow',
				id: String(row.id ?? row.slug ?? ''),
				slug: typeof row.slug === 'string' ? (row.slug as string) : undefined,
				name: String(row.name ?? row.id ?? ''),
				description:
					typeof row.description === 'string' ? (row.description as string) : undefined,
				stepsCount: steps,
				involvedApis: involved,
				importedAt: typeof row.created_at === 'number' ? row.created_at : undefined,
			};
		});
	}, [workflows]);

	// Result label semantics: when a filter is active, both API and
	// workflow counts are the server-side totals for the filtered query
	// (i.e. across *all* pages, not just the rows we've loaded into the
	// accumulator). The per-section "· N" headers continue to show
	// their own totals.
	const filteredApisCount = trimmedFilter ? apisTotal : filteredApis.length;
	const filteredWorkflowsCount = trimmedFilter ? workflowsTotal : filteredWorkflows.length;
	const totalFiltered = filteredApisCount + filteredWorkflowsCount;
	const resultsLabel = trimmedFilter ? `${totalFiltered} match "${trimmedFilter}"` : undefined;

	function openTile(entity: WorkspaceTileEntity) {
		if (entity.kind === 'api') {
			navigate(`/workspace/apis/${encodeURIComponent(entity.id)}`);
			return;
		}
		const target = entity.slug ?? entity.id;
		if (target) navigate(`/workspace/workflows/${target}`);
	}

	// ── Credential import event → cache invalidation + toast ─────────────
	//
	// Same protocol the rest of the app uses; without this a credential
	// added inside the sheet wouldn't refresh the workspace tiles' "No
	// credential" / "Credential set" meta.
	//
	// Wording note: from `/workspace`'s point of view the "imported to
	// workspace" framing is the destination message — the user is
	// already here, the API just landed. We say "credential set" rather
	// than re-announce the import to keep the toast information-dense
	// for users who are doing repeated credential management on
	// workspace APIs that were already imported.
	useEffect(() => {
		const off = subscribeCredentialImported((evt) => {
			if (!evt.api_id) return;
			queryClient.invalidateQueries({ queryKey: ['workspace', 'apis'] });
			queryClient.invalidateQueries({ queryKey: ['workspace', 'workflows'] });
			queryClient.invalidateQueries({ queryKey: ['workspace-stats'] });
			// A credential bound to an API that backs a toolkit changes the
			// toolkit card piles/counts, so refresh the enrichment too.
			queryClient.invalidateQueries({ queryKey: ['toolkit-card-enrichment'] });
			toast({
				title: 'Credential saved',
				description: `${evt.api_id} is ready to run.`,
				variant: 'success',
			});
		});
		return off;
	}, [queryClient]);

	// ── Global keyboard shortcuts ────────────────────────────────────────
	useEffect(() => {
		function onKeyDown(e: KeyboardEvent) {
			if (e.metaKey || e.ctrlKey || e.altKey) return;

			if (e.key === '/' && !isTypingTarget(e.target)) {
				e.preventDefault();
				searchRef.current?.focus();
				return;
			}

			if (e.key === 'Escape' && !isTypingTarget(e.target)) {
				if (filterInput) {
					setFilterInput('');
				}
			}
		}

		document.addEventListener('keydown', onKeyDown);
		return () => document.removeEventListener('keydown', onKeyDown);
	}, [filterInput]);

	// ── Render ───────────────────────────────────────────────────────────
	const apisLoading = apisQuery.isLoading;
	const workflowsLoading = workflowsQuery.isLoading;
	const initialLoading = apisLoading && workflowsLoading;

	return (
		<>
			<WorkspaceStatsStrip />
			<WorkspaceSearch
				ref={searchRef}
				value={filterInput}
				onChange={setFilterInput}
				resultsLabel={resultsLabel}
			/>

			{initialLoading ? (
				<WorkspaceSkeleton />
			) : (
				<div className="space-y-8" data-testid="workspace-view">
					<section data-testid="workspace-section-apis" className="space-y-3">
						<header className="flex items-baseline justify-between gap-2">
							<h2 className="text-foreground text-base font-semibold tracking-tight">
								APIs
								<span
									className="text-muted-foreground/80 ml-2 font-mono text-xs"
									data-testid="workspace-section-apis-count"
								>
									· {apisTotal.toLocaleString()}
								</span>
							</h2>
							{apisQuery.isFetching && !apisQuery.isLoading ? (
								<Loader2
									size={14}
									className="text-muted-foreground animate-spin"
									aria-label="Refreshing"
								/>
							) : null}
						</header>
						{filteredApis.length === 0 ? (
							<EmptyBlock
								filtered={Boolean(filterInput)}
								kind="api"
								onClearFilter={() => setFilterInput('')}
								onRequestImport={onRequestImport}
							/>
						) : (
							<>
								<div
									ref={apisGridRef}
									onKeyDown={onApisKeyDown}
									className="grid min-w-0 gap-3 md:grid-cols-2 xl:grid-cols-3"
									data-testid="workspace-grid-apis"
								>
									{filteredApis.map((entity) => (
										<WorkspaceTile
											key={entity.id}
											entity={entity}
											onOpen={openTile}
										/>
									))}
								</div>
								{(apisHasMore || apisIsFetchingMore) && (
									<div
										className="flex items-center justify-center pt-2"
										data-testid="workspace-apis-load-more"
									>
										{apisIsFetchingMore ? (
											<div className="text-muted-foreground flex items-center gap-2 text-sm">
												<Loader2 className="h-4 w-4 animate-spin" />
												Loading more…
											</div>
										) : (
											<Button
												variant="ghost"
												size="sm"
												onClick={() => setApisPage((p) => p + 1)}
											>
												Load more
											</Button>
										)}
									</div>
								)}
							</>
						)}
					</section>

					{workflows.length > 0 || onRequestImport ? (
						<section data-testid="workspace-section-workflows" className="space-y-3">
							<header className="flex items-baseline justify-between gap-2">
								<h2 className="text-foreground text-base font-semibold tracking-tight">
									Workflows
									<span
										className="text-muted-foreground/80 ml-2 font-mono text-xs"
										data-testid="workspace-section-workflows-count"
									>
										· {workflowsTotal.toLocaleString()}
									</span>
								</h2>
								{workflowsQuery.isFetching && !workflowsQuery.isLoading ? (
									<Loader2
										size={14}
										className="text-muted-foreground animate-spin"
										aria-label="Refreshing"
									/>
								) : null}
							</header>
							{filteredWorkflows.length === 0 ? (
								<EmptyBlock
									filtered={Boolean(filterInput)}
									kind="workflow"
									onClearFilter={() => setFilterInput('')}
									onRequestImport={onRequestImport}
								/>
							) : (
								<>
									<div
										ref={workflowsGridRef}
										onKeyDown={onWorkflowsKeyDown}
										className="grid min-w-0 gap-3 md:grid-cols-2 xl:grid-cols-3"
										data-testid="workspace-grid-workflows"
									>
										{filteredWorkflows.map((entity) => (
											<WorkspaceTile
												key={entity.id}
												entity={entity}
												onOpen={openTile}
											/>
										))}
									</div>
									{(workflowsHasMore || workflowsIsFetchingMore) && (
										<div
											className="flex items-center justify-center pt-2"
											data-testid="workspace-workflows-load-more"
										>
											{workflowsIsFetchingMore ? (
												<div className="text-muted-foreground flex items-center gap-2 text-sm">
													<Loader2 className="h-4 w-4 animate-spin" />
													Loading more…
												</div>
											) : (
												<Button
													variant="ghost"
													size="sm"
													onClick={() => setWorkflowsPage((p) => p + 1)}
												>
													Load more
												</Button>
											)}
										</div>
									)}
								</>
							)}
						</section>
					) : null}

					{toolkitCards.length > 0 ? (
						<section data-testid="workspace-section-toolkits" className="space-y-3">
							<header className="flex items-baseline justify-between gap-2">
								<h2 className="text-foreground text-base font-semibold tracking-tight">
									Toolkits
									<span
										className="text-muted-foreground/80 ml-2 font-mono text-xs"
										data-testid="workspace-section-toolkits-count"
									>
										· {toolkitCards.length.toLocaleString()}
									</span>
								</h2>
							</header>
							<div
								className="grid min-w-0 gap-3 md:grid-cols-2 xl:grid-cols-3"
								data-testid="workspace-grid-toolkits"
							>
								{toolkitCards.map((tk) => {
									const enriched = toolkitEnrichment.get(tk.id);
									return (
										<ToolkitCard
											key={tk.id}
											toolkit={{
												...tk,
												apiIds: enriched?.apiIds,
												agentCount: enriched?.agentCount,
											}}
											onOpen={toolkitSheet.openSheet}
										/>
									);
								})}
							</div>
						</section>
					) : null}
				</div>
			)}

			<WorkspaceCatalogFooter />

			<ToolkitDetailSheet
				toolkitId={toolkitSheet.stickyId}
				open={toolkitSheet.open}
				onClose={toolkitSheet.closeSheet}
				onAfterClose={toolkitSheet.clearSticky}
			/>
		</>
	);
}

function EmptyBlock({
	filtered,
	kind,
	onClearFilter,
	onRequestImport,
}: {
	filtered: boolean;
	kind: 'api' | 'workflow';
	onClearFilter: () => void;
	onRequestImport?: (tab: ImportTab) => void;
}) {
	if (filtered) {
		return (
			<div
				className="border-border/60 bg-muted/20 text-muted-foreground flex flex-col items-start gap-2 rounded-xl border border-dashed p-4 text-sm"
				data-testid={`workspace-empty-filtered-${kind}`}
			>
				<span>No {kind === 'api' ? 'APIs' : 'workflows'} match the current filter.</span>
				<button
					type="button"
					onClick={onClearFilter}
					className="text-primary hover:text-primary/80 text-xs font-medium"
				>
					Clear filter
				</button>
			</div>
		);
	}
	return (
		<div
			className="border-border/60 bg-muted/10 text-muted-foreground flex flex-col items-start gap-3 rounded-xl border border-dashed p-4 text-sm"
			data-testid={`workspace-empty-${kind}`}
		>
			{kind === 'api' ? (
				<span>You haven't added any APIs yet — import an OpenAPI spec to get started.</span>
			) : (
				<span>
					No workflows yet. Workflows orchestrate your APIs into multi-step actions.
				</span>
			)}
			{onRequestImport ? (
				<button
					type="button"
					onClick={() => onRequestImport(kind === 'api' ? 'api' : 'workflow')}
					className="text-primary hover:text-primary/80 text-xs font-semibold"
					data-testid={`workspace-empty-cta-${kind}`}
				>
					{kind === 'api' ? 'Add your first API →' : 'Add your first workflow →'}
				</button>
			) : null}
		</div>
	);
}

function WorkspaceSkeleton() {
	return (
		<div className="space-y-8">
			<section className="space-y-3">
				<Skeleton className="h-5 w-16" />
				<div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
					{Array.from({ length: 6 }).map((_, i) => (
						<div
							key={i}
							className="border-border/60 flex flex-col gap-3 rounded-xl border p-4"
						>
							<div className="flex items-center gap-3">
								<Skeleton className="h-10 w-10 rounded-lg" />
								<div className="flex-1 space-y-1.5">
									<Skeleton className="h-4 w-32" />
									<Skeleton className="h-3 w-48" />
								</div>
							</div>
							<div className="flex gap-3">
								<Skeleton className="h-3 w-14" />
								<Skeleton className="h-3 w-20" />
								<Skeleton className="h-3 w-18" />
							</div>
						</div>
					))}
				</div>
			</section>
			<section className="space-y-3">
				<Skeleton className="h-5 w-24" />
				<div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
					{Array.from({ length: 3 }).map((_, i) => (
						<div
							key={i}
							className="border-border/60 flex flex-col gap-3 rounded-xl border p-4"
						>
							<div className="flex items-center gap-3">
								<Skeleton className="h-10 w-10 rounded-lg" />
								<div className="flex-1 space-y-1.5">
									<Skeleton className="h-4 w-28" />
									<Skeleton className="h-3 w-40" />
								</div>
							</div>
							<div className="flex gap-3">
								<Skeleton className="h-3 w-16" />
								<Skeleton className="h-3 w-12" />
							</div>
						</div>
					))}
				</div>
			</section>
		</div>
	);
}
