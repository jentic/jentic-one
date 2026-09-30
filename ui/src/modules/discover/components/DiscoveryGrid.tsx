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
import { useEffect, useMemo, useRef } from 'react';
import { Compass, Upload } from 'lucide-react';
import { Button, EmptyState, ErrorAlert, Skeleton } from '@/shared/ui';
import { shellScrollRoot } from '@/shared/lib';
import { DiscoveryCard } from '@/modules/discover/components/DiscoveryCard';
import { readyCredentialsFor } from '@/modules/discover/lib/catalogRelations';
import { useAgentFigures } from '@/shared/credentials/api/apiHealth';
import type { DiscoveryEntity, WorkspaceDigestRow } from '@/modules/discover/api';

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
	/** Workspace APIs keyed by the catalog `api_id` they were imported from. */
	workspaceByCatalogId?: Map<string, WorkspaceDigestRow[]>;
	/** The credential list failed — agent figures are unknowable, not loading. */
	credentialsError?: boolean;
	/** Opens the Import-your-own-spec dialog (the empty-state CTA). */
	onImportOwn: () => void;
}

// At xl the Library docks the workspace panel beside the grid, so the grid
// column narrows — drop back to two columns until there's room for three.
const GRID_CLASS =
	'grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-2 2xl:grid-cols-3';

function SkeletonCard() {
	return (
		<div className="border-border bg-card flex h-[112px] flex-col gap-3 rounded-xl border p-3.5">
			<div className="flex items-start gap-3">
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
	workspaceByCatalogId,
	credentialsError = false,
	onImportOwn,
}: DiscoveryGridProps) {
	const sentinelRef = useRef<HTMLDivElement | null>(null);

	// Every tile's relations, derived once per data change (not per render), so
	// each memoised card gets the same props back until its facts change.
	// Bound agents are read only for tiles that show them: imported entries with
	// exactly one workspace match (the state line's "N agents").
	const singleMatches = entities.map((entity) => {
		const matches = entity.registered ? workspaceByCatalogId?.get(entity.apiId) : undefined;
		return matches?.length === 1 ? matches[0] : null;
	});
	const agentFigure = useAgentFigures(
		singleMatches.map((match) => match?.credentials ?? null).filter((c) => c != null),
		credentialsError,
	);
	const facts = useMemo(
		() =>
			new Map(
				entities.map((entity, i) => {
					const figure = singleMatches[i]
						? agentFigure(singleMatches[i].credentials)
						: null;
					return [
						entity.id,
						{
							matchAgentCount: figure?.agentCount ?? null,
							matchAgentsAtLeast: figure?.agentsAtLeast ?? false,
							matches: workspaceByCatalogId?.get(entity.apiId),
							readyCredentials: readyCredentialsFor(
								workspaceByCatalogId?.get(entity.apiId),
							),
						},
					] as const;
				}),
			),
		// eslint-disable-next-line react-hooks/exhaustive-deps -- `singleMatches` derives from entities + workspaceByCatalogId
		[entities, workspaceByCatalogId, agentFigure],
	);

	useEffect(() => {
		const node = sentinelRef.current;
		if (!node || !hasNextPage) return;
		const observer = new IntersectionObserver(
			(entries) => {
				if (entries[0]?.isIntersecting && !isFetchingNextPage) onLoadMore();
			},
			// Rooted on the shell's scroller: a viewport root's margin can't reach past
			// `<main>`'s clip, so the prefetch would wait for the sentinel to show.
			{ root: shellScrollRoot(), rootMargin: '200px' },
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
						? "Try a different search term, or switch the filter. Can't find it?"
						: 'The public catalog will appear here.'
				}
				action={
					<Button
						variant="outline"
						size="sm"
						onClick={onImportOwn}
						data-testid="discover-empty-upload-own"
					>
						<Upload size={14} aria-hidden="true" />
						Import your own API
					</Button>
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
						workspaceMatches={facts.get(entity.id)?.matches}
						matchAgentCount={facts.get(entity.id)?.matchAgentCount ?? null}
						matchAgentsAtLeast={facts.get(entity.id)?.matchAgentsAtLeast ?? false}
						readyCredentials={facts.get(entity.id)?.readyCredentials}
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
