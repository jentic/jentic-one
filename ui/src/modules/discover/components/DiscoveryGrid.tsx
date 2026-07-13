/**
 * DiscoveryGrid — the responsive card grid with loading / empty / error states
 * and keyset infinite scroll.
 *
 * Stateless presentational shell: the page owns data + handlers and passes
 * entities in. Loading shows skeleton cards (no layout shift); empty shows the
 * shared EmptyState; errors surface the shared ErrorAlert. When `hasNextPage`
 * is set, an IntersectionObserver sentinel calls `onLoadMore` as it scrolls
 * into view (with a shared Button fallback for keyboard/no-IO environments).
 */
import { useEffect, useRef } from 'react';
import { Compass } from 'lucide-react';
import { Button, EmptyState, ErrorAlert, Skeleton } from '@/shared/ui';
import { DiscoveryCard } from '@/modules/discover/components/DiscoveryCard';
import type { DiscoveryEntity } from '@/modules/discover/api';

interface DiscoveryGridProps {
	entities: DiscoveryEntity[];
	loading: boolean;
	error: Error | null;
	activeId: string | null;
	onOpen: (entity: DiscoveryEntity) => void;
	onImport: (entity: DiscoveryEntity) => void;
	/** Catalog api_ids with an import job still settling (Available → Pending). */
	pendingApiIds: Set<string>;
	/** Shown in the empty state to clarify whether a search is active. */
	hasQuery: boolean;
	/** Whether another keyset page is available. */
	hasNextPage: boolean;
	/** True while the next page is being fetched. */
	isFetchingNextPage: boolean;
	/** Request the next keyset page. */
	onLoadMore: () => void;
}

const GRID_CLASS = 'grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3';

function SkeletonCard() {
	return (
		<div className="border-border bg-card flex h-[148px] flex-col gap-3 rounded-xl border p-5">
			<div className="flex items-start gap-4">
				<Skeleton className="h-10 w-10 rounded-[10px]" />
				<div className="flex-1 space-y-2">
					<Skeleton className="h-4 w-2/3" />
					<Skeleton className="h-3 w-full" />
				</div>
			</div>
			<Skeleton className="mt-auto h-5 w-20 rounded-full" />
		</div>
	);
}

export function DiscoveryGrid({
	entities,
	loading,
	error,
	activeId,
	onOpen,
	onImport,
	pendingApiIds,
	hasQuery,
	hasNextPage,
	isFetchingNextPage,
	onLoadMore,
}: DiscoveryGridProps) {
	const sentinelRef = useRef<HTMLDivElement | null>(null);

	useEffect(() => {
		const node = sentinelRef.current;
		if (!node || !hasNextPage) return;
		const observer = new IntersectionObserver(
			(entries) => {
				if (entries[0]?.isIntersecting && !isFetchingNextPage) onLoadMore();
			},
			{ rootMargin: '200px' },
		);
		observer.observe(node);
		return () => observer.disconnect();
	}, [hasNextPage, isFetchingNextPage, onLoadMore]);

	if (error) {
		return <ErrorAlert message={error.message} />;
	}

	if (loading && entities.length === 0) {
		return (
			<div className={GRID_CLASS} data-testid="discovery-grid-loading" aria-busy="true">
				{Array.from({ length: 6 }).map((_, i) => (
					<SkeletonCard key={i} />
				))}
			</div>
		);
	}

	if (entities.length === 0) {
		return (
			<EmptyState
				icon={<Compass className="h-6 w-6" aria-hidden="true" />}
				title={hasQuery ? 'No matching APIs' : 'No APIs yet'}
				description={
					hasQuery
						? 'Try a different search term, or switch the filter.'
						: 'The public catalog will appear here.'
				}
			/>
		);
	}

	return (
		<div className="flex flex-col gap-4">
			<div className={GRID_CLASS} data-testid="discovery-grid">
				{entities.map((entity) => (
					<DiscoveryCard
						key={entity.id}
						entity={entity}
						active={entity.id === activeId}
						onOpen={onOpen}
						onImport={onImport}
						importPending={pendingApiIds.has(entity.apiId)}
					/>
				))}
			</div>

			{hasNextPage && (
				<div ref={sentinelRef} className="flex justify-center py-2">
					<Button
						variant="ghost"
						size="sm"
						loading={isFetchingNextPage}
						onClick={onLoadMore}
						data-testid="discovery-load-more"
					>
						{isFetchingNextPage ? 'Loading…' : 'Load more'}
					</Button>
				</div>
			)}
		</div>
	);
}
