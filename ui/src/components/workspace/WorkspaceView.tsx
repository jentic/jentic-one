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
import { Skeleton } from '@/components/ui/Skeleton';
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

function matchesQuery(haystack: string | null | undefined, needle: string): boolean {
	if (!needle) return true;
	if (!haystack) return false;
	return haystack.toLowerCase().includes(needle.toLowerCase());
}

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

	// ── Workspace data ────────────────────────────────────────────────────
	//
	// One paginated APIs probe (we ask for a generous first page; users with
	// >60 APIs are extremely rare on the Mini surface — and if they hit it,
	// the sub-section will simply add a "Load more" button rather than an
	// infinite-scroll sentinel that fights the page's scroll model).
	const apisQuery = useQuery({
		queryKey: ['workspace', 'apis', 1, WORKSPACE_PAGE_SIZE],
		queryFn: () => api.listApis(1, WORKSPACE_PAGE_SIZE, 'local'),
		staleTime: 30_000,
	});
	const apisPayload = apisQuery.data as
		| { data?: Array<Record<string, unknown>>; total?: number }
		| undefined;
	const apis = useMemo<Array<Record<string, unknown>>>(
		() => (Array.isArray(apisPayload?.data) ? apisPayload.data : []),
		[apisPayload],
	);
	const apisTotal = apisPayload?.total ?? apis.length;

	const workflowsQuery = useQuery({
		queryKey: ['workspace', 'workflows'],
		queryFn: () => api.listWorkflows(undefined, 'local'),
		staleTime: 60_000,
	});
	const workflows = useMemo<Array<Record<string, unknown>>>(
		() => (Array.isArray(workflowsQuery.data) ? workflowsQuery.data : []),
		[workflowsQuery.data],
	);

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

	// One credentials lookup per toolkit. The `enabled` gate keeps these
	// from firing until the parent toolkits list resolves.
	const toolkitCredsQueries = useQuery({
		queryKey: [
			'workspace',
			'toolkit-credentials',
			toolkits
				.map((t) => t.id)
				.sort()
				.join(','),
		],
		queryFn: async () => {
			const entries = await Promise.all(
				toolkits.map(async (t) => {
					try {
						const creds = await api.listToolkitCredentials(t.id);
						return [t, Array.isArray(creds) ? creds : []] as const;
					} catch {
						return [t, []] as const;
					}
				}),
			);
			return entries;
		},
		enabled: toolkits.length > 0,
		staleTime: 60_000,
	});

	const apiToolkitMap = useMemo<Map<string, string[]>>(() => {
		const map = new Map<string, string[]>();
		const data = toolkitCredsQueries.data;
		if (!data) return map;
		for (const [toolkit, creds] of data) {
			if (toolkit.id === 'default') continue;
			const seenForTk = new Set<string>();
			for (const cred of creds as Array<{ api_id?: unknown }>) {
				const apiId = typeof cred?.api_id === 'string' ? cred.api_id : null;
				if (!apiId || seenForTk.has(apiId)) continue;
				seenForTk.add(apiId);
				const existing = map.get(apiId) ?? [];
				if (!existing.includes(toolkit.name)) existing.push(toolkit.name);
				map.set(apiId, existing);
			}
		}
		return map;
	}, [toolkitCredsQueries.data]);

	const hasDefaultToolkit = toolkits.some((t) => t.id === 'default');

	// ── In-memory filter ──────────────────────────────────────────────────
	const [filterInput, setFilterInput] = useState('');
	const searchRef = useRef<WorkspaceSearchHandle>(null);
	const apisGridRef = useRef<HTMLDivElement>(null);
	const workflowsGridRef = useRef<HTMLDivElement>(null);
	const onApisKeyDown = useRovingGridFocus(apisGridRef, 'button[data-testid="workspace-tile-api"]');
	const onWorkflowsKeyDown = useRovingGridFocus(workflowsGridRef, 'button[data-testid="workspace-tile-workflow"]');
	const filteredApis = useMemo<WorkspaceTileEntity[]>(() => {
		const q = filterInput.trim();
		return apis
			.filter((row) => {
				const name = String(row.name ?? row.id ?? '');
				const description = String(row.description ?? '');
				return matchesQuery(name, q) || matchesQuery(description, q);
			})
			.map((row) => {
				const id = String(row.id ?? '');
				const explicitToolkits = apiToolkitMap.get(id) ?? [];
				const hasCreds = Boolean(row.has_credentials);
				const toolkitCount =
					explicitToolkits.length + (hasDefaultToolkit && hasCreds ? 1 : 0);
				const toolkitNames = Array.from({ length: toolkitCount }, (_, i) =>
					i < explicitToolkits.length ? explicitToolkits[i] : 'default',
				);
				return {
					kind: 'api',
					id,
					name: String(row.name ?? row.id ?? ''),
					description:
						typeof row.description === 'string'
							? (row.description as string)
							: undefined,
					hasCredentials: hasCreds,
					toolkitNames,
					operationCount: typeof row.operation_count === 'number' ? row.operation_count : undefined,
					credentialCount: typeof row.credential_count === 'number' ? row.credential_count : undefined,
					workflowCount: typeof row.workflow_count === 'number' ? row.workflow_count : undefined,
					importedAt: typeof row.created_at === 'number' ? row.created_at : undefined,
				};
			});
	}, [apis, filterInput, apiToolkitMap, hasDefaultToolkit]);

	const filteredWorkflows = useMemo<WorkspaceTileEntity[]>(() => {
		const q = filterInput.trim();
		return workflows
			.filter((row) => {
				const name = String(row.name ?? row.id ?? '');
				const description = String(row.description ?? '');
				return matchesQuery(name, q) || matchesQuery(description, q);
			})
			.map((row) => {
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
						typeof row.description === 'string'
							? (row.description as string)
							: undefined,
					stepsCount: steps,
					involvedApis: involved,
					importedAt: typeof row.created_at === 'number' ? row.created_at : undefined,
				};
			});
	}, [workflows, filterInput]);

	const totalFiltered = filteredApis.length + filteredWorkflows.length;
	const totalUnfiltered = apis.length + workflows.length;
	const resultsLabel = filterInput
		? `${totalFiltered} of ${totalUnfiltered} match "${filterInput.trim()}"`
		: undefined;

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
										· {workflows.length.toLocaleString()}
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
							)}
						</section>
					) : null}
				</div>
			)}

			<WorkspaceCatalogFooter />
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
