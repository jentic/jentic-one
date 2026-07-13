/**
 * OperationPreviewList — the searchable, paginated, clickable operations list
 * inside the API detail sheet.
 *
 * Renders the server-paginated preview from `GET /catalog/{api_id}/operations`.
 * Search + tag filtering are SERVER-SIDE (the sheet owns the `q`/`tag` state and
 * re-queries), so they cover every operation in the spec, not just the loaded
 * page. The list grows 25 at a time via the "Load more" footer. Each row is a
 * button that drills into the operation detail via `onSelect`.
 */
import { useMemo } from 'react';
import { MethodBadge, Skeleton } from '@/shared/ui';
import {
	OperationsListFooter,
	OperationsListToolbar,
	topTags,
	type OpRow,
} from '@/modules/discover/components/OperationsListControls';
import type { PreviewOperationResponse } from '@/modules/discover/api';

interface OperationPreviewListProps {
	operations: PreviewOperationResponse[];
	loading: boolean;
	error: Error | null;
	/** Full (filtered) operation count in the spec. */
	total: number;
	/** Controlled search text (drives server-side `q`). */
	filter: string;
	onFilterChange: (next: string) => void;
	/** Controlled active tag (drives server-side `tag`). */
	activeTag: string | null;
	onTagChange: (next: string | null) => void;
	hasNextPage: boolean;
	isFetchingNextPage: boolean;
	onLoadMore: () => void;
	/** Drill into a single operation. Receives the op's stable row key. */
	onSelect: (key: string) => void;
}

/** Stable per-op key shared by the list rows and the sheet's selection lookup. */
export function opKey(op: PreviewOperationResponse, index: number): string {
	return op.operation_id ?? `${op.method}-${op.path}-${index}`;
}

function OperationRow({ op, onSelect }: { op: OpRow; onSelect: (key: string) => void }) {
	return (
		<li>
			<button
				type="button"
				onClick={() => onSelect(op.key)}
				className="hover:bg-muted/50 flex w-full items-start gap-3 rounded-md px-2 py-2 text-left transition-colors"
				data-testid="operations-row"
			>
				<div className="shrink-0 pt-0.5">
					<MethodBadge method={op.method ?? ''} />
				</div>
				<div className="min-w-0 flex-1">
					<code className="text-foreground block truncate font-mono text-xs">
						{op.path}
					</code>
					{op.label && op.label !== op.path && (
						<p className="text-muted-foreground mt-0.5 line-clamp-2 text-sm">
							{op.label}
						</p>
					)}
				</div>
			</button>
		</li>
	);
}

export function OperationPreviewList({
	operations,
	loading,
	error,
	total,
	filter,
	onFilterChange,
	activeTag,
	onTagChange,
	hasNextPage,
	isFetchingNextPage,
	onLoadMore,
	onSelect,
}: OperationPreviewListProps) {
	const rows: OpRow[] = useMemo(
		() =>
			operations.map((op, i) => ({
				key: opKey(op, i),
				method: op.method,
				path: op.path,
				label: op.summary || op.path,
				tags: op.tags ?? [],
			})),
		[operations],
	);

	// Tag chips are derived from the loaded ops (they grow as you Load more);
	// selecting one drives the server-side `tag` filter over the whole spec.
	const tags = useMemo(() => topTags(rows.flatMap((r) => r.tags)), [rows]);

	// A filter is active when the user has typed a search or picked a tag.
	const filtering = filter.trim().length > 0 || activeTag !== null;

	if (error) {
		return (
			<p className="text-destructive text-sm" role="alert">
				{error.message}
			</p>
		);
	}

	return (
		<div data-testid="operations-list">
			<OperationsListToolbar
				filter={filter}
				onFilterChange={onFilterChange}
				tags={tags}
				activeTag={activeTag}
				onTagChange={onTagChange}
				totalOps={total}
			/>
			{loading ? (
				<div className="space-y-2" aria-busy="true" data-testid="operations-loading">
					{Array.from({ length: 5 }).map((_, i) => (
						<Skeleton key={i} className="h-12 w-full" />
					))}
				</div>
			) : rows.length === 0 ? (
				<p className="text-muted-foreground py-2 text-sm">
					{filtering
						? 'No operations match your filter.'
						: "No operations found in this API's spec."}
				</p>
			) : (
				<>
					<ul className="space-y-0.5">
						{rows.map((op) => (
							<OperationRow key={op.key} op={op} onSelect={onSelect} />
						))}
					</ul>
					<OperationsListFooter
						loaded={rows.length}
						total={total}
						hasNextPage={hasNextPage}
						isFetchingNextPage={isFetchingNextPage}
						onLoadMore={onLoadMore}
					/>
				</>
			)}
		</div>
	);
}
