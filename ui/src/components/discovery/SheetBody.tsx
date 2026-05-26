import { useMemo, useState, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ExternalLink, Loader2, Plus } from 'lucide-react';
import { SectionTitle } from './SectionTitle';
import { ApiSummary } from './ApiSummary';
import {
	OPS_PAGE_SIZE,
	topTags,
	filterOps,
	OperationsListToolbar,
	OperationsListFooter,
} from './OperationsListControls';
import type { OpRow } from './OperationsListControls';
import type { DiscoveryEntity } from './DiscoveryCard';
import { MethodBadge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { AppLink } from '@/components/ui/AppLink';
import { Skeleton } from '@/components/ui/Skeleton';
import { WorkflowRow } from '@/components/ui/WorkflowRow';
import { api } from '@/api/client';
import { directoryOpKey } from '@/lib/directoryOpKey';
import { useImportCatalogApi } from '@/hooks/useImportCatalogApi';

export interface SheetBodyProps {
	apiId: string;
	initialEntity?: DiscoveryEntity;
	source: 'workspace' | 'directory';
	/** While true, defer rendering the import/open CTA — we are still
	 *  resolving the authoritative source from the workspace endpoint. */
	sourceResolving?: boolean;
	onSelectOp: (opId: string) => void;
	onSelectWf: (wfId: string | null) => void;
}

export function SheetBody({
	apiId,
	initialEntity,
	source,
	sourceResolving = false,
	onSelectOp,
	onSelectWf,
}: SheetBodyProps) {
	const githubUrl: string | undefined = initialEntity?.raw?._links?.github;
	const specUrl: string | undefined =
		initialEntity?.specUrl ?? initialEntity?.raw?.spec_url ?? undefined;
	const { importApi, pendingApiId } = useImportCatalogApi();
	const isImporting = pendingApiId === apiId;

	const [pages, setPages] = useState<number>(1);
	const [filter, setFilter] = useState('');
	const [activeTag, setActiveTag] = useState<string | null>(null);
	const prevApiIdRef = useRef(apiId);
	const accumulatorRef = useRef<any[]>([]);
	const seenKeysRef = useRef<Set<string>>(new Set());

	if (prevApiIdRef.current !== apiId) {
		prevApiIdRef.current = apiId;
		accumulatorRef.current = [];
		seenKeysRef.current = new Set();
		setPages(1);
		setFilter('');
		setActiveTag(null);
	}

	const offset = (pages - 1) * OPS_PAGE_SIZE;

	const { data, isLoading, error, isFetching } = useQuery({
		queryKey: ['sheet-ops-catalog', apiId, offset],
		queryFn: () => api.previewCatalogOperations(apiId, { offset, limit: OPS_PAGE_SIZE }),
		staleTime: 5 * 60_000,
		retry: 1,
	});

	// When user types a filter and we haven't loaded all ops yet, fetch the rest
	const prelimTotal = data?.total ?? 0;
	const needsAll = (filter.trim() !== '' || activeTag !== null) && prelimTotal > 0;
	const { data: allOpsData } = useQuery({
		queryKey: ['sheet-ops-catalog-all', apiId],
		queryFn: async () => {
			const all: any[] = [];
			let off = 0;
			const batchSize = 200;
			while (true) {
				const batch = await api.previewCatalogOperations(apiId, {
					offset: off,
					limit: batchSize,
				});
				all.push(...(batch.data ?? []));
				if (all.length >= (batch.total ?? 0) || (batch.data?.length ?? 0) < batchSize)
					break;
				off += batchSize;
			}
			return { data: all, total: all.length };
		},
		staleTime: 5 * 60_000,
		enabled: needsAll,
	});

	const operations = useMemo(() => {
		if (needsAll && allOpsData?.data?.length) {
			return allOpsData.data;
		}
		const incoming = data?.data ?? [];
		if (incoming.length === 0) return accumulatorRef.current;
		let changed = false;
		const next = [...accumulatorRef.current];
		for (const op of incoming) {
			const key = directoryOpKey(op.method, op.path);
			if (seenKeysRef.current.has(key)) continue;
			seenKeysRef.current.add(key);
			next.push(op);
			changed = true;
		}
		if (changed) {
			accumulatorRef.current = next;
		}
		return accumulatorRef.current;
	}, [data, needsAll, allOpsData]);

	const total = data?.total ?? operations.length;
	const hasMore = !needsAll && operations.length < total;
	const isFetchingMore = isFetching && pages > 1;
	const specDescription = data?.info?.description ?? initialEntity?.description;

	const rows: OpRow[] = useMemo(
		() =>
			operations.map((op: any) => ({
				key: directoryOpKey(op.method, op.path),
				method: op.method,
				path: op.path,
				label: op.summary || op.operation_id || op.path,
				tags: Array.isArray(op.tags) ? op.tags : [],
			})),
		[operations],
	);

	const tagOptions = useMemo(() => topTags(rows.flatMap((r) => r.tags)), [rows]);
	const visible = useMemo(() => filterOps(rows, filter, activeTag), [rows, filter, activeTag]);

	return (
		<div className="space-y-5 p-5">
			<div className="space-y-3">
				<ApiSummary
					description={specDescription}
					title={initialEntity?.summary}
					host={apiId}
					opCount={total}
					tagCount={tagOptions.length}
				/>

				<div className="flex flex-wrap items-center gap-2">
					{sourceResolving ? (
						<Skeleton className="h-9 w-40 rounded-lg" />
					) : source === 'directory' ? (
						<Button
							onClick={() => {
								if (isImporting) return;
								void importApi({ apiId, specUrl });
							}}
							disabled={isImporting}
							data-testid="sheet-directory-import"
						>
							{isImporting ? (
								<>
									<Loader2 size={14} className="animate-spin" />
									Importing…
								</>
							) : (
								<>
									<Plus size={14} />
									Import to workspace
								</>
							)}
						</Button>
					) : (
						<AppLink
							href={`/workspace/apis/${encodeURIComponent(apiId)}`}
							className="bg-primary text-primary-foreground hover:bg-primary/90 inline-flex h-9 items-center gap-1.5 rounded-lg px-3 text-sm font-medium transition-colors"
						>
							Open in Workspace
						</AppLink>
					)}
					{githubUrl && (
						<AppLink
							href={githubUrl}
							className="border-border text-muted-foreground hover:text-foreground hover:bg-muted inline-flex h-9 items-center gap-1.5 rounded-lg border px-3 text-sm font-medium transition-colors"
						>
							<ExternalLink size={14} /> GitHub
						</AppLink>
					)}
				</div>

				{!sourceResolving && source === 'directory' && (
					<p className="text-muted-foreground/80 text-xs">
						Importing registers the spec locally so operations become browsable. Add
						credentials from Workspace to make them runnable.
					</p>
				)}
			</div>

			<SheetWorkflowsSection apiId={apiId} source={source} onSelectWf={onSelectWf} />

			<section>
				{isLoading && rows.length === 0 ? (
					<SheetBodySkeleton />
				) : (
					<>
						<SectionTitle count={total}>Operations</SectionTitle>

						{error && (
							<p className="text-danger text-sm">
								Failed to fetch the OpenAPI spec.{' '}
								{(error as Error).message ? `(${(error as Error).message})` : null}
							</p>
						)}

						{!isLoading && !error && rows.length === 0 && (
							<p className="text-muted-foreground text-sm">
								No operations found in the spec.
							</p>
						)}

						{rows.length > 0 && (
							<>
								<OperationsListToolbar
									filter={filter}
									onFilterChange={setFilter}
									tags={tagOptions}
									activeTag={activeTag}
									onTagChange={setActiveTag}
									totalOps={total}
								/>

								{visible.length === 0 ? (
									<p className="text-muted-foreground py-3 text-sm">
										No operations match the current filter.
									</p>
								) : (
									<ul
										className="divide-border/40 -mx-2 divide-y"
										data-testid="sheet-ops-list"
									>
										{visible.map((row) => (
											<li key={row.key}>
												<button
													type="button"
													onClick={() => onSelectOp(row.key)}
													className="hover:bg-muted/50 flex w-full items-start gap-3 rounded-md px-2 py-2 text-left transition-colors"
													data-testid="sheet-ops-row"
												>
													<MethodBadge method={row.method} />
													<div className="min-w-0 flex-1">
														<p className="text-foreground truncate text-sm font-medium">
															{row.label}
														</p>
														<code className="text-muted-foreground block truncate font-mono text-xs">
															{row.path}
														</code>
													</div>
												</button>
											</li>
										))}
									</ul>
								)}

								<OperationsListFooter
									visible={visible.length}
									loaded={operations.length}
									total={total}
									hasMore={hasMore}
									isFetchingMore={isFetchingMore}
									onLoadMore={() => setPages((p) => p + 1)}
								/>
							</>
						)}
					</>
				)}
			</section>
		</div>
	);
}

// ── Workflows section (unified — works for both sources) ──────────────────────

function SheetWorkflowsSection({
	apiId,
	source,
	onSelectWf,
}: {
	apiId: string;
	source: 'workspace' | 'directory';
	onSelectWf: (wfId: string | null) => void;
}) {
	const catalogQuery = useQuery({
		queryKey: ['sheet-workflows-catalog', apiId],
		queryFn: () => api.previewCatalogWorkflows(apiId),
		staleTime: 5 * 60_000,
		retry: 1,
		enabled: source === 'directory',
	});

	const localQuery = useQuery({
		queryKey: ['workflows', 'local'],
		queryFn: () => api.listWorkflows(undefined, 'local'),
		staleTime: 10_000,
		enabled: source === 'workspace',
	});

	const allWorkflows: Array<{
		slug: string;
		name?: string;
		steps_count?: number;
		involved_apis?: string[];
	}> = useMemo(() => {
		if (source !== 'workspace') return [];
		const raw = Array.isArray(localQuery.data) ? localQuery.data : [];
		if (raw.length === 0) return [];
		const apiLower = apiId.toLowerCase();
		return raw.filter((wf: { involved_apis?: unknown }) => {
			if (!Array.isArray(wf.involved_apis)) return false;
			return wf.involved_apis.some(
				(a: string) =>
					a === apiId ||
					a.toLowerCase().includes(apiLower) ||
					apiLower.includes(a.toLowerCase()),
			);
		});
	}, [localQuery.data, apiId, source]);

	if (source === 'directory') {
		const workflows = catalogQuery.data?.data ?? [];
		if (catalogQuery.isLoading) return null;
		if (catalogQuery.error) {
			return (
				<section>
					<p className="text-muted-foreground text-sm">
						Workflows couldn't be loaded.{' '}
						<button
							type="button"
							onClick={() => catalogQuery.refetch()}
							className="text-primary hover:underline"
						>
							Retry
						</button>
					</p>
				</section>
			);
		}
		if (workflows.length === 0) return null;

		return (
			<section data-testid="sheet-workflows-section">
				<SectionTitle count={workflows.length}>Workflows</SectionTitle>
				<ul className="divide-border/40 -mx-2 divide-y" data-testid="sheet-wf-list">
					{workflows.map((wf) => {
						const label = wf.summary ?? wf.workflow_id;
						const description = wf.description?.trim() ?? '';
						return (
							<li key={wf.slug} data-testid="sheet-wf-row">
								<WorkflowRow
									name={label}
									description={description}
									stepsCount={wf.steps_count}
									onClick={() => onSelectWf(wf.slug)}
								/>
							</li>
						);
					})}
				</ul>
				{catalogQuery.data?.github_url && (
					<AppLink
						href={catalogQuery.data.github_url}
						className="text-muted-foreground hover:text-foreground mt-2 inline-flex items-center gap-1 text-xs"
					>
						<ExternalLink size={11} /> View on GitHub
					</AppLink>
				)}
			</section>
		);
	}

	if (localQuery.isLoading) return null;
	if (allWorkflows.length === 0) return null;

	return (
		<section data-testid="sheet-workflows-section">
			<SectionTitle count={allWorkflows.length}>Workflows</SectionTitle>
			<ul className="divide-border/40 -mx-2 divide-y" data-testid="sheet-wf-list">
				{allWorkflows.map((wf) => (
					<li key={wf.slug} data-testid="sheet-wf-row">
						<WorkflowRow
							name={wf.name ?? wf.slug}
							stepsCount={wf.steps_count}
							onClick={() => onSelectWf(wf.slug)}
						/>
					</li>
				))}
			</ul>
		</section>
	);
}

// ── Skeletons ─────────────────────────────────────────────────────────────────

function SheetBodySkeleton() {
	return (
		<div className="space-y-4">
			{Array.from({ length: 4 }).map((_, i) => (
				<div key={i} className="flex items-start gap-3 py-2">
					<Skeleton className="h-4 w-4 shrink-0 rounded" />
					<div className="flex-1 space-y-1.5">
						<Skeleton className="h-3.5 w-3/5" />
						<Skeleton className="h-3 w-4/5" />
					</div>
				</div>
			))}
		</div>
	);
}
