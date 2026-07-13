/**
 * OperationsSection — operations of an API's current (live) revision.
 *
 * Ported from jentic-mini's API-detail operations list, adapted to jentic-one:
 *  - the backend has no operations search param, so to filter across *every*
 *    operation we load all cursor pages in the background and run the filter
 *    client-side over the full set; the list itself only ever paints one page
 *    of 25 (Prev/Next), so filtering matches everything while the view stays
 *    capped at 25 rows;
 *  - a first-class **no live revision** state (the backend 404s with
 *    `no_current_revision` when the API is draft-only), which routes the user
 *    to the Revisions section to promote a draft rather than showing an error.
 *
 * Rows match the Discover module's visual language (method badge + mono path +
 * summary, hover-highlighted) and expand into the shared `OperationDetail`
 * Parameters/Auth tables. The slim operations endpoint has no params/security,
 * so that detail is read out of the resolved OpenAPI spec (fetched lazily once
 * the list resolves) and looked up per operation.
 */
import { useMemo, useState } from 'react';
import {
	Card,
	CardHeader,
	CardTitle,
	CardBody,
	MethodBadge,
	Badge,
	Button,
	Skeleton,
	EmptyState,
	ErrorAlert,
	SearchInput,
	OperationDetail,
	type OperationDetailData,
} from '@/shared/ui';
import { ChevronDown, ChevronLeft, ChevronRight, Filter, ListTree, Loader2 } from 'lucide-react';
import { cn } from '@/shared/lib/utils';
import {
	useApiOperations,
	useApiSpec,
	parseSpecOperations,
	opDetailKey,
	WorkspaceApiError,
} from '@/modules/workspace/api';
import type { ApiKey, ApiOperation, ParsedSpec } from '@/modules/workspace/api';

/** Operations rendered per page (the filter still spans all loaded ops). */
const PAGE_SIZE = 25;

function OperationRow({ operation, spec }: { operation: ApiOperation; spec: ParsedSpec | null }) {
	const [expanded, setExpanded] = useState(false);
	const detail = spec?.operations.get(opDetailKey(operation.method, operation.path));

	const data: OperationDetailData = {
		method: operation.method,
		path: operation.path,
		// The enclosing row already renders the method/path/name, so the inline
		// detail only adds the description + Parameters/Auth tables (showHeader
		// false, no summary) to avoid repeating the heading.
		description: operation.description ?? undefined,
		parameters: detail?.parameters ?? [],
		security: detail?.security ?? [],
	};

	const hasDetail =
		Boolean(data.description) || data.parameters.length > 0 || data.security.length > 0;

	return (
		<li data-testid="operation-row">
			<button
				type="button"
				onClick={() => setExpanded((v) => !v)}
				aria-expanded={expanded}
				className="hover:bg-muted/50 focus-visible:ring-primary/40 flex w-full items-start gap-3 rounded-md px-2 py-2 text-left transition-colors focus-visible:ring-2 focus-visible:outline-none"
			>
				<div className="shrink-0 pt-0.5">
					<MethodBadge method={operation.method} />
				</div>
				<div className="min-w-0 flex-1">
					<code className="text-foreground block truncate font-mono text-xs">
						{operation.path}
					</code>
					{operation.name && operation.name !== operation.path ? (
						<p className="text-muted-foreground mt-0.5 line-clamp-2 text-sm">
							{operation.name}
						</p>
					) : null}
				</div>
				{operation.deprecated ? (
					<Badge variant="warning" className="mt-0.5 shrink-0">
						Deprecated
					</Badge>
				) : null}
				<ChevronDown
					size={14}
					aria-hidden="true"
					className={cn(
						'text-muted-foreground mt-1 shrink-0 transition-transform',
						expanded && 'rotate-180',
					)}
				/>
			</button>
			{expanded ? (
				<div className="border-border/40 mt-1 mb-2 ml-3 border-l-2 pl-4">
					{hasDetail ? (
						<OperationDetail
							operation={data}
							securitySchemes={spec?.securitySchemes}
							showHeader={false}
						/>
					) : (
						<p className="text-muted-foreground py-1 text-xs">
							No further detail available. View the full spec for parameters and
							schemas.
						</p>
					)}
				</div>
			) : null}
		</li>
	);
}

export function OperationsSection({
	apiKey,
	totalCount,
}: {
	apiKey: ApiKey;
	/**
	 * The API's known `operation_count`, shown as the total. The operations
	 * endpoint's page envelope carries no total, so this comes from the API
	 * detail and is the authoritative figure even before every page loads.
	 */
	totalCount?: number;
}) {
	const query = useApiOperations(apiKey, totalCount);
	const [filter, setFilter] = useState('');

	const operations = query.operations;

	// Lazily pull the resolved spec once operations resolve, so we can show the
	// shared Parameters/Auth tables on expand. Failure is non-fatal — rows just
	// render without the detail tables.
	const hasOps = operations.length > 0;
	const specQuery = useApiSpec(apiKey, hasOps);
	const spec = useMemo<ParsedSpec | null>(
		() => (specQuery.data != null ? parseSpecOperations(specQuery.data) : null),
		[specQuery.data],
	);

	const filtered = useMemo(() => {
		const needle = filter.trim().toLowerCase();
		if (!needle) return operations;
		return operations.filter(
			(op) =>
				op.path.toLowerCase().includes(needle) ||
				op.method.toLowerCase().includes(needle) ||
				(op.name ?? '').toLowerCase().includes(needle),
		);
	}, [operations, filter]);

	const noCurrentRevision =
		query.error instanceof WorkspaceApiError && query.error.isNoCurrentRevision;

	const filtering = filter.trim().length > 0;
	// Loaded so far vs the API's authoritative `operation_count`. The walk may
	// still be running, so these differ mid-load.
	const loaded = operations.length;
	const total = totalCount ?? loaded;
	// A mid-walk fetch error leaves a partial list (some pages already loaded).
	// The top-level error state only covers the no-pages-at-all case, so here we
	// show a non-fatal inline retry instead of discarding what we have.
	const partialLoadError = query.isError && loaded > 0;

	// The list always paints a single page of PAGE_SIZE, paging through the
	// (already exhaustive once loaded) filtered set. Reset to the first page
	// whenever the filter changes — done during render (not in an effect) so the
	// first post-change paint already shows page 0, avoiding a stale-slice frame.
	const [page, setPage] = useState(0);
	const [pagedFilter, setPagedFilter] = useState(filter);
	if (pagedFilter !== filter) {
		setPagedFilter(filter);
		setPage(0);
	}

	const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
	// Clamp if the result set shrank under us (e.g. mid background-load) so we
	// never land on an empty page past the end. Page controls work off `safePage`
	// so a click is never swallowed by a stale `page` index.
	const safePage = Math.min(page, pageCount - 1);
	const pageStart = safePage * PAGE_SIZE;
	const visible = filtered.slice(pageStart, pageStart + PAGE_SIZE);
	const pageEnd = pageStart + visible.length;
	const goPrev = () => setPage(Math.max(0, safePage - 1));
	const goNext = () => setPage(Math.min(pageCount - 1, safePage + 1));

	return (
		<Card data-testid="operations-section">
			<CardHeader className="flex flex-wrap items-center justify-between gap-3">
				<CardTitle className="flex items-center gap-2">
					Operations
					{total > 0 ? (
						<span className="text-muted-foreground text-sm font-normal tabular-nums">
							{total}
						</span>
					) : null}
				</CardTitle>
				{operations.length > 0 ? (
					<SearchInput
						value={filter}
						onValueChange={setFilter}
						icon={<Filter className="h-3.5 w-3.5" />}
						placeholder="Filter operations…"
						className="max-w-xs"
						aria-label="Filter operations"
					/>
				) : null}
			</CardHeader>
			<CardBody>
				{query.isLoading ? (
					<div className="space-y-2" aria-busy="true">
						{Array.from({ length: 5 }).map((_, i) => (
							<Skeleton key={i} className="h-9 w-full" />
						))}
					</div>
				) : noCurrentRevision ? (
					<EmptyState
						icon={<ListTree size={28} aria-hidden="true" />}
						title="No live revision yet"
						description="This API has a draft revision but nothing promoted. Promote a revision below to publish its operations."
					/>
				) : query.isError && operations.length === 0 ? (
					<ErrorAlert
						message={
							query.error instanceof Error
								? query.error
								: 'Failed to load operations.'
						}
					/>
				) : filtered.length === 0 ? (
					<EmptyState
						icon={<ListTree size={28} aria-hidden="true" />}
						title={filter ? 'No operations match your filter' : 'No operations'}
						description={
							filter
								? query.isLoadingAll
									? 'No match yet — the remaining operations are still loading.'
									: 'Try a different search term.'
								: 'This revision exposes no operations.'
						}
					/>
				) : (
					<>
						<ul className="space-y-0.5" data-testid="operations-list">
							{visible.map((op) => (
								<OperationRow
									key={`${op.method}-${op.path}-${op.operationId}`}
									operation={op}
									spec={spec}
								/>
							))}
						</ul>
						{partialLoadError ? (
							<div
								className="border-warning/20 bg-warning/10 text-warning mt-3 flex flex-wrap items-center justify-between gap-2 rounded-md border px-3 py-2 text-xs"
								role="alert"
								data-testid="operations-partial-error"
							>
								<span>
									Couldn’t load the remaining operations. Showing the {loaded}{' '}
									loaded so far.
								</span>
								<Button
									variant="secondary"
									size="sm"
									onClick={query.retry}
									data-testid="operations-retry"
								>
									Retry
								</Button>
							</div>
						) : null}
						<div className="border-border/40 mt-3 flex flex-wrap items-center justify-between gap-3 border-t pt-3">
							<div className="text-muted-foreground flex items-center gap-2 text-xs">
								{query.isLoadingAll ? (
									<Loader2
										size={12}
										aria-hidden="true"
										className="animate-spin"
									/>
								) : null}
								<span data-testid="operations-count">
									{filtering
										? query.isLoadingAll
											? `${filtered.length} match in ${loaded} loaded of ${total}`
											: `${filtered.length} of ${total} match · showing ${pageStart + 1}–${pageEnd}`
										: `Showing ${pageStart + 1}–${pageEnd} of ${total}`}
									{query.isLoadingAll ? ' — loading the rest…' : ''}
								</span>
							</div>
							{pageCount > 1 ? (
								<div className="flex items-center gap-2">
									<Button
										variant="secondary"
										size="sm"
										onClick={goPrev}
										disabled={safePage === 0}
										aria-label="Previous page"
										data-testid="operations-prev-page"
									>
										<ChevronLeft size={14} aria-hidden="true" />
									</Button>
									<span
										className="text-muted-foreground text-xs tabular-nums"
										data-testid="operations-page-indicator"
									>
										{safePage + 1} / {pageCount}
									</span>
									<Button
										variant="secondary"
										size="sm"
										onClick={goNext}
										disabled={safePage >= pageCount - 1}
										aria-label="Next page"
										data-testid="operations-next-page"
									>
										<ChevronRight size={14} aria-hidden="true" />
									</Button>
								</div>
							) : null}
						</div>
					</>
				)}
			</CardBody>
		</Card>
	);
}
