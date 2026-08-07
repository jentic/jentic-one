/**
 * RevisionsSection — an API's revision history with promote/archive actions.
 *
 * jentic-one models an API as a stack of revisions; exactly one can be "live"
 * (`is_current`). A freshly imported API starts as a single `draft` revision
 * with nothing live — promoting it publishes its operations. This section lists
 * revisions newest-first, badges their state, marks the live one, and offers
 * promote/archive where the backend's `_links` advertise those actions.
 *
 * Legibility: each row also carries its ORIGIN (import / catalog / upload /
 * `overlay ovr_…`, cross-linked to the producing overlay below), a one-line
 * "what changed" (operation-count delta vs the previous revision), the
 * submitting principal, and a Diff affordance that opens the spec viewer in
 * diff mode (vs live for historical rows, vs the previous revision for the
 * live one) instead of only a full-spec dump.
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
	CopyButton,
	ActorLabel,
} from '@/shared/ui';
import { FileDiff, GitBranch, Layers } from 'lucide-react';
import type { BadgeVariant } from '@/shared/ui';
import { SpecViewerDialog } from '@/modules/workspace/components/SpecViewerDialog';
import type { SpecDiffBase } from '@/modules/workspace/components/SpecViewerDialog';
import {
	useApiRevisions,
	useOverlays,
	useRevisionActions,
	overlayForRevision,
	revisionOriginLabel,
	revisionChangeSummary,
	shortRevisionId,
	summarizeOverlayActions,
} from '@/modules/workspace/api';
import type { ApiKey, ApiRevision, Overlay, RevisionState } from '@/modules/workspace/api';

/** Badge colour per known lifecycle state; unknown wire values fall back to `default`. */
const STATE_VARIANT: Partial<Record<RevisionState, BadgeVariant>> = {
	published: 'success',
	draft: 'pending',
	archived: 'default',
};

function stateVariant(state: RevisionState): BadgeVariant {
	return STATE_VARIANT[state] ?? 'default';
}

/** Compact absolute datetime (e.g. "7 Aug 2026, 10:52"). */
function formatDateTime(iso: string): string {
	const ts = Date.parse(iso);
	if (Number.isNaN(ts)) return iso;
	return new Date(ts).toLocaleString(undefined, {
		day: 'numeric',
		month: 'short',
		year: 'numeric',
		hour: '2-digit',
		minute: '2-digit',
	});
}

/** Scroll the producing overlay's row into view (rows carry `data-overlay-id`). */
function scrollToOverlay(overlayId: string) {
	document
		.querySelector(`[data-overlay-id="${CSS.escape(overlayId)}"]`)
		?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

type RevisionAction = 'promote' | 'archive' | null;

function RevisionRow({
	revision,
	previous,
	producedBy,
	onPromote,
	onArchive,
	onViewSpec,
	pendingAction,
}: {
	revision: ApiRevision;
	/** The revision created just before this one (list is newest-first). */
	previous: ApiRevision | null;
	/** The overlay whose confirm materialized this revision, when known. */
	producedBy: Overlay | null;
	onPromote: (id: string) => void;
	onArchive: (id: string) => void;
	onViewSpec: (revision: ApiRevision) => void;
	/** Which action (if any) is in flight for *this* row — spins only that button. */
	pendingAction: RevisionAction;
}) {
	// The first line of the producing overlay's action summary doubles as the
	// revision's "what changed" story (e.g. "Remove the US-only servers block").
	const overlayNote = producedBy
		? (summarizeOverlayActions(producedBy.document)[0] ?? null)
		: null;

	return (
		<li
			className="border-border/60 flex flex-wrap items-center gap-3 border-b py-3 last:border-b-0"
			data-testid="revision-row"
			data-revision-id={revision.revisionId}
		>
			<div className="min-w-0 flex-1">
				<div className="flex flex-wrap items-center gap-2">
					<Badge variant={stateVariant(revision.state)}>{revision.state}</Badge>
					{revision.isCurrent ? <Badge variant="success">Live</Badge> : null}
					<span
						className="text-muted-foreground truncate font-mono text-xs"
						title={revision.revisionId}
					>
						{shortRevisionId(revision.revisionId)}
					</span>
					<CopyButton
						value={revision.revisionId}
						size="icon"
						variant="ghost"
						ariaLabel="Copy full revision id"
						toastMessage="Revision id copied"
					/>
					{producedBy ? (
						<Button
							variant="ghost"
							size="sm"
							onClick={() => scrollToOverlay(producedBy.id)}
							title="Jump to the overlay that produced this revision"
							data-testid="revision-origin-overlay"
						>
							<Layers size={12} aria-hidden="true" />
							{revisionOriginLabel(revision, producedBy.id)}
						</Button>
					) : (
						<span
							className="text-muted-foreground text-xs"
							data-testid="revision-origin"
						>
							{revisionOriginLabel(revision, null)}
						</span>
					)}
				</div>
				<p className="text-muted-foreground mt-1 text-xs" data-testid="revision-summary">
					{revisionChangeSummary(revision, previous)}
					{overlayNote ? ` · ${overlayNote}` : ''}
					{revision.submittedBy ? (
						<>
							{' · by '}
							<ActorLabel actorId={revision.submittedBy} />
						</>
					) : null}
					{` · ${formatDateTime(revision.createdAt)}`}
				</p>
			</div>
			<div className="flex shrink-0 gap-2">
				<Button
					variant="ghost"
					size="sm"
					onClick={() => onViewSpec(revision)}
					data-testid="revision-view-spec"
				>
					<FileDiff size={14} aria-hidden="true" />
					Diff
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

/**
 * The comparison base for a revision's diff: a historical revision diffs vs
 * LIVE ("what would change if this served"), the live revision diffs vs the
 * revision created just before it ("what the last change did"). Null when
 * there is nothing to compare against (single-revision API).
 */
function diffBaseFor(revision: ApiRevision, revisions: ApiRevision[]): SpecDiffBase | null {
	const live = revisions.find((r) => r.isCurrent) ?? null;
	if (!revision.isCurrent && live) {
		return {
			revisionId: live.revisionId,
			label: `live · ${shortRevisionId(live.revisionId)}`,
		};
	}
	const index = revisions.findIndex((r) => r.revisionId === revision.revisionId);
	const previous = index >= 0 ? (revisions[index + 1] ?? null) : null;
	if (previous) {
		return {
			revisionId: previous.revisionId,
			label: `previous · ${shortRevisionId(previous.revisionId)}`,
		};
	}
	return null;
}

export function RevisionsSection({ apiKey }: { apiKey: ApiKey }) {
	const query = useApiRevisions(apiKey);
	// Shared cache with OverlaysSection — used to cross-link overlay-origin
	// revisions to the overlay that produced them. Best-effort: while loading
	// (or on error) rows simply render the plain origin label.
	const overlaysQuery = useOverlays(apiKey);
	const { promote, archive, pendingRevisionId, pendingAction } = useRevisionActions(apiKey);
	const [specRevision, setSpecRevision] = useState<ApiRevision | null>(null);

	const revisions = query.data?.items ?? [];
	const overlays = overlaysQuery.data?.items ?? [];

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
						{revisions.map((rev, index) => (
							<RevisionRow
								key={rev.revisionId}
								revision={rev}
								previous={revisions[index + 1] ?? null}
								producedBy={overlayForRevision(overlays, rev.revisionId)}
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
						? `${specRevision.state}${specRevision.isCurrent ? ' · live' : ''} · ${shortRevisionId(specRevision.revisionId)}`
						: undefined
				}
				diffAgainst={specRevision ? diffBaseFor(specRevision, revisions) : null}
			/>
		</Card>
	);
}
