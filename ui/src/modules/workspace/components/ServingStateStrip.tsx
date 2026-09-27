/**
 * ServingStateStrip — "what is serving right now, and who last changed it",
 * shown above the API detail tabs, e.g.:
 *
 *   Serving revision dc9bcdeb (imported) · 2 overlays (0 active) · last
 *   change: overlay submitted Aug 7 by Ada
 *   [ 1 overlay waiting for review — Review ]
 *
 * In a shared workspace the attribution matters: the API you're looking at
 * may have been changed by someone else since you last saw it, and a pending
 * overlay is a change someone is waiting on you (or a colleague) to review.
 *
 * Reads the same revision/overlay queries the sections below use (shared
 * TanStack cache — no extra requests) and derives the line with the pure
 * `describeServingState`, whose "current" comes from the revisions list alone
 * (single source of truth — no separate `current_revision_id` prop that could
 * disagree mid-refetch). Renders nothing until BOTH background page walks
 * finish: a partially-loaded list would present undercounts as fact.
 */
import { Activity, GitPullRequestArrow } from 'lucide-react';
import { ActorLabel, Button } from '@/shared/ui';
import {
	useApiRevisions,
	useOverlays,
	describeServingState,
	lastChangeEvent,
	pendingOverlayCount,
} from '@/modules/workspace/api';
import type { ApiKey } from '@/modules/workspace/api';

export interface ServingStateStripProps {
	apiKey: ApiKey;
	/** Jump to the overlays list (the "Revisions & overlays" tab). */
	onReviewOverlays?: () => void;
}

export function ServingStateStrip({ apiKey, onReviewOverlays }: ServingStateStripProps) {
	const revisions = useApiRevisions(apiKey);
	const overlays = useOverlays(apiKey);

	const ready =
		!revisions.isLoading &&
		!revisions.isLoadingAll &&
		!revisions.isError &&
		!overlays.isLoading &&
		!overlays.isLoadingAll &&
		!overlays.isError;
	if (!ready) return null;

	const line = describeServingState(revisions.items, overlays.items);
	const lastChange = lastChangeEvent(revisions.items, overlays.items);
	const pending = pendingOverlayCount(overlays.items);

	return (
		<div className="space-y-2" data-testid="serving-state-strip">
			<p className="text-muted-foreground flex items-start gap-2 text-sm">
				<Activity size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
				<span>
					{line}
					{lastChange?.actorId ? (
						<>
							{' '}
							by{' '}
							<ActorLabel actorId={lastChange.actorId} className="text-foreground" />
						</>
					) : null}
				</span>
			</p>
			{pending > 0 ? (
				<div
					className="border-warning/30 bg-warning/5 flex flex-wrap items-center justify-between gap-3 rounded-lg border px-3 py-2"
					data-testid="pending-overlays-callout"
				>
					<span className="text-foreground flex items-center gap-2 text-sm">
						<GitPullRequestArrow
							size={14}
							aria-hidden="true"
							className="text-warning shrink-0"
						/>
						{pending} overlay{pending === 1 ? '' : 's'} waiting for review. Nothing
						changes for agents until one is confirmed.
					</span>
					{onReviewOverlays ? (
						<Button variant="secondary" size="sm" onClick={onReviewOverlays}>
							Review
						</Button>
					) : null}
				</div>
			) : null}
		</div>
	);
}
