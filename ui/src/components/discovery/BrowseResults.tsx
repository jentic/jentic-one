import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import { DiscoveryCard } from './DiscoveryCard';
import type { DiscoveryEntity, DiscoverySource } from './DiscoveryCard';
import { DiscoverEmptyState } from './DiscoverEmptyState';
import { apiToEntity } from './adapters';
import { api } from '@/api/client';
import { Button } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { useInfiniteScroll } from '@/hooks/useInfiniteScroll';
import { useRovingGridFocus } from '@/hooks/useRovingGridFocus';
import { subscribeApiImported } from '@/lib/events/apiImported';

const BROWSE_PAGE_SIZE = 24;

export function BrowseResults({
	query,
	expandedId,
	onCardClick,
	forcedSource,
	emptyMode = 'page',
	onShownCountChange,
	onImport,
	importPendingApiId,
}: {
	/** When set, filters APIs by name/id substring. */
	query?: string;
	expandedId: string | null;
	onCardClick: (entity: DiscoveryEntity) => void;
	/**
	 * Required source axis. Every production caller hard-codes this
	 * (`/discover` → `'directory'`, `/workspace` sectioned mode →
	 * `'workspace'` for the workspace section and `'directory'` for the
	 * catalog section). The user-facing source toggle was deleted in the
	 * May 2026 simplification, so there is no longer a `urlSource`
	 * fallback path.
	 */
	forcedSource: DiscoverySource;
	emptyMode?: 'page' | 'inline';
	onShownCountChange?: (shown: number | null) => void;
	onImport?: (entity: DiscoveryEntity) => void;
	importPendingApiId?: string | null;
}) {
	const source = forcedSource;

	const serverSource: string = source === 'workspace' ? 'local' : 'catalog';

	const includeImported = forcedSource === 'directory';

	const [page, setPage] = useState(1);
	const [accumulator, setAccumulator] = useState<any[]>([]);

	// Reset pagination when source or query changes
	const queryKeyRef = useRef(`${source}|${query}`);
	useEffect(() => {
		const key = `${source}|${query}`;
		if (key !== queryKeyRef.current) {
			queryKeyRef.current = key;
			setPage(1);
		}
	}, [source, query]);

	const apisQuery = useQuery({
		queryKey: [
			'apis',
			'discover',
			serverSource,
			page,
			BROWSE_PAGE_SIZE,
			includeImported,
			query || null,
		],
		queryFn: () =>
			api.listApis(page, BROWSE_PAGE_SIZE, serverSource, query || undefined, {
				includeImported,
			}),
		staleTime: 30000,
		placeholderData: (prev) => prev,
	});

	const apisPage = apisQuery.data as
		| { data?: any[]; total?: number; total_pages?: number }
		| undefined;

	useEffect(() => {
		if (apisQuery.isPlaceholderData) return;
		if (!apisPage?.data) return;
		const incoming = apisPage.data;
		if (incoming.length === 0 && page === 1) {
			setAccumulator([]);
			return;
		}
		if (incoming.length === 0) return;
		if (page === 1) {
			setAccumulator(incoming);
			return;
		}
		setAccumulator((prev) => {
			const indexById = new Map<string, number>();
			prev.forEach((row, idx) => {
				const id: string = row?.id ?? '';
				if (id) indexById.set(id, idx);
			});
			const next = prev.slice();
			for (const row of incoming) {
				const id: string = row.id ?? '';
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
	}, [apisPage, page, apisQuery.isPlaceholderData]);

	// Optimistically flip a row from directory → workspace on import.
	// Subscribes to the canonical `apiImported` channel introduced in
	// the credentials revamp v2 split — the legacy `credentialImported`
	// dual-emit was removed in v3 cleanup, so subscribers that care
	// about API arrival (this one) listen here, while subscribers that
	// care about credential arrival (toasts in WorkspaceView) listen on
	// `credentialImported`.
	useEffect(() => {
		const off = subscribeApiImported((evt) => {
			if (!evt.api_id) return;
			setAccumulator((prev) => {
				const idx = prev.findIndex((row) => row?.id === evt.api_id);
				if (idx === -1) return prev;
				const row = prev[idx];
				if (row.source === 'local') return prev;
				const next = prev.slice();
				next[idx] = { ...row, source: 'local' };
				return next;
			});
		});
		return off;
	}, []);

	const entities: DiscoveryEntity[] = useMemo(() => accumulator.map(apiToEntity), [accumulator]);

	const isInitialLoading = apisQuery.isLoading && accumulator.length === 0;

	useEffect(() => {
		if (!onShownCountChange) return;
		if (isInitialLoading) onShownCountChange(null);
		else onShownCountChange(entities.length);
	}, [onShownCountChange, isInitialLoading, entities.length]);

	const gridRef = useRef<HTMLDivElement | null>(null);
	const onKeyDown = useRovingGridFocus(gridRef, 'button[data-testid^="discovery-card-"]');

	const totalCount = apisPage?.total ?? entities.length;
	const totalPages = apisPage?.total_pages ?? 1;
	const hasMore = page < totalPages;
	const isFetchingMore = apisQuery.isFetching && page > 1;

	const sentinelRef = useInfiniteScroll({
		hasMore,
		isLoading: apisQuery.isFetching,
		onLoadMore: () => setPage((p) => p + 1),
	});

	if (isInitialLoading) return <DiscoverGridSkeleton />;

	if (entities.length === 0) {
		if (query) {
			return <DiscoverEmptyState variant="zero-search" query={query} />;
		}

		if (emptyMode === 'inline' && forcedSource === 'workspace') {
			return <DiscoverEmptyState variant="cold-start-sectioned" />;
		}
		// Both `forcedSource === 'directory'` (any emptyMode) and the
		// `'workspace'` page-mode branch land on the catalog-degraded
		// state — the latter only happens for the workspace section in
		// sectioned mode (never page-mode in production).
		return <DiscoverEmptyState variant="catalog-degraded" />;
	}

	const showInfiniteScroll = hasMore || isFetchingMore;

	return (
		<div className="space-y-4">
			{query && (
				<p aria-live="polite" className="text-muted-foreground text-xs">
					{totalCount} result{totalCount !== 1 ? 's' : ''} for "{query}"
					{apisQuery.isFetching && <span className="text-primary ml-2">Updating…</span>}
				</p>
			)}
			<div
				ref={gridRef}
				onKeyDown={onKeyDown}
				className="grid gap-3 md:grid-cols-2 xl:grid-cols-3"
				data-testid={`browse-grid-${forcedSource}`}
			>
				{entities.map((entity) => (
					<DiscoveryCard
						key={entity.id}
						entity={entity}
						expanded={expandedId === entity.id}
						onToggle={() => onCardClick(entity)}
						onImport={onImport}
						importPending={importPendingApiId === entity.id}
					/>
				))}
			</div>
			{showInfiniteScroll && (
				<div
					ref={sentinelRef}
					data-testid="browse-infinite-sentinel"
					className="flex items-center justify-center py-4"
					aria-live="polite"
				>
					{isFetchingMore ? (
						<div className="text-muted-foreground flex items-center gap-2 text-sm">
							<Loader2 className="h-4 w-4 animate-spin" />
							Loading more…
						</div>
					) : (
						<Button variant="ghost" size="sm" onClick={() => setPage((p) => p + 1)}>
							Load more
						</Button>
					)}
				</div>
			)}
			{!hasMore && entities.length > 0 && totalCount > BROWSE_PAGE_SIZE && (
				<p className="text-muted-foreground/70 text-center text-xs">
					Showing all {totalCount.toLocaleString()} APIs
				</p>
			)}
		</div>
	);
}

function DiscoverGridSkeleton() {
	return (
		<div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
			{Array.from({ length: 9 }).map((_, i) => (
				<div
					key={i}
					className="border-border bg-card flex flex-col gap-3 rounded-xl border p-4"
				>
					<div className="flex items-center gap-3">
						<Skeleton className="h-10 w-10 rounded-xl" />
						<div className="flex-1 space-y-1.5">
							<Skeleton className="h-4 w-28" />
							<Skeleton className="h-3 w-44" />
						</div>
						<Skeleton className="h-5 w-16 rounded-full" />
					</div>
					<div className="space-y-1.5">
						<Skeleton className="h-3 w-full" />
						<Skeleton className="h-3 w-3/4" />
					</div>
					<div className="flex gap-2">
						<Skeleton className="h-5 w-14 rounded-full" />
						<Skeleton className="h-5 w-18 rounded-full" />
					</div>
				</div>
			))}
		</div>
	);
}
