/**
 * ApiList — the workspace APIs as a dense list of `ApiRow`s, with its own
 * loading/empty/error states. Pure presentation: the page owns the data and
 * the joins (traffic, credentials, attention); the list renders what it's
 * given.
 *
 * Rows rather than cards: a shared workspace grows to dozens of APIs, and a
 * row keeps the figures that matter in aligned columns you can scan down.
 */
import { Boxes } from 'lucide-react';
import { Card, Skeleton, EmptyState, ErrorAlert, Button } from '@/shared/ui';
import { API_ROW_GRID, ApiRow } from '@/modules/workspace/components/ApiRow';
import { USAGE_WINDOW_DAYS } from '@/modules/workspace/api';
import type { ApiRowProps } from '@/modules/workspace/components/ApiRow';

export interface ApiListProps {
	rows: ApiRowProps[];
	isLoading: boolean;
	isError: boolean;
	error?: unknown;
	onRetry?: () => void;
	/** Rendered inside the empty state (e.g. import + browse-the-catalog CTAs). */
	emptyAction?: React.ReactNode;
	/** True when a filter is active, so the empty copy says "no matches". */
	filtered?: boolean;
}

function ColumnHeader() {
	return (
		<div
			aria-hidden="true"
			className={`border-border/60 text-muted-foreground/80 hidden border-b px-4 py-2 text-[11px] font-medium tracking-wide uppercase ${API_ROW_GRID}`}
		>
			<span>API</span>
			<span>Serving</span>
			<span>Calls · {USAGE_WINDOW_DAYS}d</span>
			<span>Credentials</span>
			<span>Updated</span>
			<span />
		</div>
	);
}

export function ApiList({
	rows,
	isLoading,
	isError,
	error,
	onRetry,
	emptyAction,
	filtered,
}: ApiListProps) {
	if (isLoading) {
		return (
			<Card className="divide-border/60 divide-y" aria-busy="true">
				{Array.from({ length: 5 }).map((_, i) => (
					<div key={i} className="px-4 py-3">
						<Skeleton className="h-9 w-full rounded-lg" />
					</div>
				))}
			</Card>
		);
	}

	if (isError) {
		return (
			<div className="space-y-3" data-testid="workspace-list-error">
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

	if (rows.length === 0) {
		return (
			<EmptyState
				icon={<Boxes size={32} aria-hidden="true" />}
				title={filtered ? 'No APIs match your filter' : 'No APIs in your workspace yet'}
				description={
					filtered
						? 'Try a different search, or clear the filter to see everything.'
						: 'Import one from the public catalog, or bring your own OpenAPI spec. Everyone in this workspace — and the agents you grant — can then use it.'
				}
				action={filtered ? undefined : emptyAction}
			/>
		);
	}

	return (
		<Card className="overflow-hidden p-0" data-testid="workspace-list">
			<ColumnHeader />
			<ul className="divide-border/60 divide-y">
				{rows.map((row) => (
					<li key={`${row.api.api.vendor}/${row.api.api.name}/${row.api.api.version}`}>
						<ApiRow {...row} />
					</li>
				))}
			</ul>
		</Card>
	);
}
