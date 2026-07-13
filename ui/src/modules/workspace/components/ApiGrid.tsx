/**
 * ApiGrid — responsive grid of `ApiCard`s with its own loading/empty/error
 * states. Pure presentation: the page owns the data + the import CTA, the grid
 * just renders the rows it's given.
 */
import { Skeleton, EmptyState, ErrorAlert, Button } from '@/shared/ui';
import { Boxes } from 'lucide-react';
import { ApiCard } from '@/modules/workspace/components/ApiCard';
import type { WorkspaceApi } from '@/modules/workspace/api';

const GRID = 'grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3';

export interface ApiGridProps {
	apis: WorkspaceApi[];
	isLoading: boolean;
	isError: boolean;
	error?: unknown;
	onRetry?: () => void;
	/** Rendered inside the empty state (e.g. an "Import a spec" button). */
	emptyAction?: React.ReactNode;
	/** True when a filter is active, so the empty copy says "no matches". */
	filtered?: boolean;
}

export function ApiGrid({
	apis,
	isLoading,
	isError,
	error,
	onRetry,
	emptyAction,
	filtered,
}: ApiGridProps) {
	if (isLoading) {
		return (
			<div className={GRID} data-testid="workspace-grid-loading" aria-busy="true">
				{Array.from({ length: 6 }).map((_, i) => (
					<Skeleton key={i} className="h-40 w-full rounded-xl" />
				))}
			</div>
		);
	}

	if (isError) {
		return (
			<div className="space-y-3" data-testid="workspace-grid-error">
				<ErrorAlert
					message={error instanceof Error ? error : 'Failed to load your APIs.'}
				/>
				{onRetry ? (
					<Button variant="secondary" size="sm" onClick={onRetry}>
						Try again
					</Button>
				) : null}
			</div>
		);
	}

	if (apis.length === 0) {
		return (
			<EmptyState
				icon={<Boxes size={32} aria-hidden="true" />}
				title={filtered ? 'No APIs match your filter' : 'No APIs in your workspace yet'}
				description={
					filtered
						? 'Try a different search, or clear the filter to see everything.'
						: 'Import an OpenAPI spec to register your first API.'
				}
				action={filtered ? undefined : emptyAction}
			/>
		);
	}

	return (
		<div className={GRID} data-testid="workspace-grid">
			{apis.map((api) => (
				<ApiCard key={`${api.api.vendor}/${api.api.name}/${api.api.version}`} api={api} />
			))}
		</div>
	);
}
