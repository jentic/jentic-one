/**
 * RevisionsSection — an API's revision history with promote/archive actions.
 *
 * jentic-one models an API as a stack of revisions; exactly one can be "live"
 * (`is_current`). A freshly imported API starts as a single `draft` revision
 * with nothing live — promoting it publishes its operations. This section lists
 * revisions newest-first, badges their state, marks the live one, and offers
 * promote/archive where the backend's `_links` advertise those actions.
 */
import { useState } from 'react';
import {
	Card,
	CardHeader,
	CardTitle,
	CardBody,
	Badge,
	Button,
	Skeleton,
	EmptyState,
	ErrorAlert,
} from '@/shared/ui';
import { FileJson, GitBranch } from 'lucide-react';
import type { BadgeVariant } from '@/shared/ui';
import { SpecViewerDialog } from '@/modules/workspace/components/SpecViewerDialog';
import { useApiRevisions, useRevisionActions } from '@/modules/workspace/api';
import type { ApiKey, ApiRevision, RevisionState } from '@/modules/workspace/api';

/** Badge colour per known lifecycle state; unknown wire values fall back to `default`. */
const STATE_VARIANT: Partial<Record<RevisionState, BadgeVariant>> = {
	published: 'success',
	draft: 'pending',
	archived: 'default',
};

function stateVariant(state: RevisionState): BadgeVariant {
	return STATE_VARIANT[state] ?? 'default';
}

type RevisionAction = 'promote' | 'archive' | null;

function RevisionRow({
	revision,
	onPromote,
	onArchive,
	onViewSpec,
	pendingAction,
}: {
	revision: ApiRevision;
	onPromote: (id: string) => void;
	onArchive: (id: string) => void;
	onViewSpec: (revision: ApiRevision) => void;
	/** Which action (if any) is in flight for *this* row — spins only that button. */
	pendingAction: RevisionAction;
}) {
	return (
		<li
			className="border-border/60 flex flex-wrap items-center gap-3 border-b py-3 last:border-b-0"
			data-testid="revision-row"
		>
			<div className="min-w-0 flex-1">
				<div className="flex items-center gap-2">
					<Badge variant={stateVariant(revision.state)}>{revision.state}</Badge>
					{revision.isCurrent ? <Badge variant="success">Live</Badge> : null}
					<span className="text-muted-foreground truncate font-mono text-xs">
						{revision.revisionId.slice(0, 8)}
					</span>
				</div>
				<p className="text-muted-foreground mt-1 text-xs">
					{revision.operationCount} operation{revision.operationCount === 1 ? '' : 's'}
					{revision.sourceType ? ` · ${revision.sourceType}` : ''}
				</p>
			</div>
			<div className="flex shrink-0 gap-2">
				<Button
					variant="ghost"
					size="sm"
					onClick={() => onViewSpec(revision)}
					data-testid="revision-view-spec"
				>
					<FileJson size={14} aria-hidden="true" />
					View spec
				</Button>
				{revision.promoteHref ? (
					<Button
						variant="secondary"
						size="sm"
						onClick={() => onPromote(revision.revisionId)}
						loading={pendingAction === 'promote'}
						data-testid="revision-promote"
					>
						Promote
					</Button>
				) : null}
				{revision.archiveHref ? (
					<Button
						variant="ghost"
						size="sm"
						onClick={() => onArchive(revision.revisionId)}
						loading={pendingAction === 'archive'}
						data-testid="revision-archive"
					>
						Archive
					</Button>
				) : null}
			</div>
		</li>
	);
}

export function RevisionsSection({ apiKey }: { apiKey: ApiKey }) {
	const query = useApiRevisions(apiKey);
	const { promote, archive, pendingRevisionId, pendingAction } = useRevisionActions(apiKey);
	const [specRevision, setSpecRevision] = useState<ApiRevision | null>(null);

	const revisions = query.data?.items ?? [];

	return (
		<Card data-testid="revisions-section">
			<CardHeader>
				<CardTitle>Revisions</CardTitle>
			</CardHeader>
			<CardBody>
				{query.isLoading ? (
					<div className="space-y-2" aria-busy="true">
						{Array.from({ length: 3 }).map((_, i) => (
							<Skeleton key={i} className="h-12 w-full" />
						))}
					</div>
				) : query.isError ? (
					<ErrorAlert
						message={
							query.error instanceof Error ? query.error : 'Failed to load revisions.'
						}
					/>
				) : revisions.length === 0 ? (
					<EmptyState
						icon={<GitBranch size={28} aria-hidden="true" />}
						title="No revisions"
						description="This API has no revisions yet."
					/>
				) : (
					<ul className="divide-border/60">
						{revisions.map((rev) => (
							<RevisionRow
								key={rev.revisionId}
								revision={rev}
								onPromote={promote}
								onArchive={archive}
								onViewSpec={setSpecRevision}
								pendingAction={
									pendingRevisionId === rev.revisionId ? pendingAction : null
								}
							/>
						))}
					</ul>
				)}
			</CardBody>

			<SpecViewerDialog
				apiKey={apiKey}
				open={specRevision !== null}
				onClose={() => setSpecRevision(null)}
				revisionId={specRevision?.revisionId ?? null}
				revisionLabel={
					specRevision
						? `${specRevision.state}${specRevision.isCurrent ? ' · live' : ''} · ${specRevision.revisionId.slice(0, 8)}`
						: undefined
				}
			/>
		</Card>
	);
}
