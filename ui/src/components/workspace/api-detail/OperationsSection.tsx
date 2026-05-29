import { OperationRow } from './OperationRow';
import { OperationsSkeleton } from './skeletons';
import { SectionTitle } from '@/components/discovery/SectionTitle';
import {
	OperationsListFooter,
	OperationsListToolbar,
} from '@/components/discovery/OperationsListControls';
import type { OpRow } from '@/components/discovery/OperationsListControls';

interface OperationsSectionProps {
	rows: OpRow[];
	visible: OpRow[];
	loaded: number;
	total: number;
	tagOptions: string[];
	filter: string;
	onFilterChange: (value: string) => void;
	activeTag: string | null;
	onTagChange: (tag: string | null) => void;
	expandedOps: Set<string>;
	onToggleExpanded: (key: string) => void;
	isLoading: boolean;
	hasMore: boolean;
	isFetchingMore: boolean;
	onLoadMore: () => void;
}

/**
 * Renders the operations block (toolbar + list + footer). All state
 * is owned by `useApiOperations` so the orchestrator can surface the
 * totals to peer sections without prop-drilling callbacks.
 */
export function OperationsSection({
	rows,
	visible,
	loaded,
	total,
	tagOptions,
	filter,
	onFilterChange,
	activeTag,
	onTagChange,
	expandedOps,
	onToggleExpanded,
	isLoading,
	hasMore,
	isFetchingMore,
	onLoadMore,
}: OperationsSectionProps) {
	return (
		<section>
			<SectionTitle count={total}>Operations</SectionTitle>

			{isLoading && rows.length === 0 ? (
				<OperationsSkeleton />
			) : rows.length === 0 ? (
				<p className="text-muted-foreground mt-3 text-sm">No operations found.</p>
			) : (
				<div className="mt-3">
					<OperationsListToolbar
						filter={filter}
						onFilterChange={onFilterChange}
						tags={tagOptions}
						activeTag={activeTag}
						onTagChange={onTagChange}
						totalOps={total}
					/>

					{visible.length === 0 ? (
						<p className="text-muted-foreground py-3 text-sm">
							No operations match the current filter.
						</p>
					) : (
						<ul className="divide-border/40 -mx-2 divide-y" data-testid="ops-list">
							{visible.map((row) => (
								<OperationRow
									key={row.key}
									row={row}
									expanded={expandedOps.has(row.key)}
									onToggle={() => onToggleExpanded(row.key)}
								/>
							))}
						</ul>
					)}

					<OperationsListFooter
						visible={visible.length}
						loaded={loaded}
						total={total}
						hasMore={hasMore}
						isFetchingMore={isFetchingMore}
						onLoadMore={onLoadMore}
					/>
				</div>
			)}
		</section>
	);
}
