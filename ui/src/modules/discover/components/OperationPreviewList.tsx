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
import { Button, MethodBadge, Skeleton } from '@/shared/ui';
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
			<Button
				variant="ghost"
				onClick={() => onSelect(op.key)}
				// A two-row grid: method chip + path, then the summary under the path.
				className="rounded-field hover:bg-tint -mx-2.5 grid h-auto w-[calc(100%+1.25rem)] grid-cols-[58px_1fr] items-start justify-items-start gap-x-2.5 gap-y-0.5 px-2.5 py-[9px] text-left font-normal active:scale-100"
				data-testid="operations-row"
			>
				<MethodBadge method={op.method} />
				<code className="text-foreground-lighter block w-full min-w-0 truncate font-mono text-[12.5px] leading-5">
					{op.path}
				</code>
				{op.label && op.label !== op.path && (
					<span className="text-foreground-faint col-start-2 line-clamp-2 text-[12.5px]">
						{op.label}
					</span>
				)}
			</Button>
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

	// With nothing to filter (no ops and no filter applied), the filter field and
	// tag bar would only add noise above the empty note.
	const showToolbar = loading || filtering || rows.length > 0;

	return (
		<div data-testid="operations-list">
			{showToolbar && (
				<OperationsListToolbar
					filter={filter}
					onFilterChange={onFilterChange}
					tags={tags}
					activeTag={activeTag}
					onTagChange={onTagChange}
				/>
			)}
			{loading ? (
				<div className="space-y-2" aria-busy="true" data-testid="operations-loading">
					{Array.from({ length: 5 }).map((_, i) => (
						<Skeleton key={i} className="h-11 w-full" />
					))}
				</div>
			) : rows.length === 0 ? (
				<p className="text-muted-foreground py-2 text-[13.5px]">
					{filtering
						? 'No operations match your filter.'
						: "No operations found in this API's spec."}
				</p>
			) : (
				<>
					<ul>
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
