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
 * diff mode against the SAME previous revision the summary describes (the
 * API's first revision has no base, so its button reads "View spec" instead).
 */
import { useState } from 'react';
import {
	Card,
	CardHeader,
	CardTitle,
	CardBody,
	Badge,
	Button,
	SkeletonRows,
	EmptyState,
	ErrorAlert,
	CopyButton,
	ActorLabel,
	toast,
} from '@/shared/ui';
import { FileDiff, FileJson, GitBranch, Layers } from 'lucide-react';
import type { BadgeVariant } from '@/shared/ui';
import { SpecViewerDialog } from '@/modules/workspace/components/SpecViewerDialog';
import { jumpToOverlay } from '@/modules/workspace/components/jumpToRow';
import {
	useApiRevisions,
	useOverlays,
	useRevisionActions,
	diffBaseFor,
	formatDateTime,
	overlayForRevision,
	revisionOriginLabel,
	revisionChangeSummary,
	revisionStateLabel,
	shortOverlayId,
	shortRevisionId,
	summarizeOverlayActions,
} from '@/modules/workspace/api';
import type { ApiKey, ApiRevision, Overlay, RevisionState } from '@/modules/workspace/api';

/** Badge colour per known lifecycle state; unknown wire values fall back to `default`. */
const STATE_VARIANT: Partial<Record<RevisionState, BadgeVariant>> = {
	imported: 'success',
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
	previous,
	producedBy,
	hasDiffBase,
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
	/** Whether a diff base exists — the first-ever revision only has a full spec. */
	hasDiffBase: boolean;
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
	const shortId = shortRevisionId(revision.revisionId);

	return (
		<li
			className="border-border/60 flex flex-col gap-2 border-b py-3 last:border-b-0 sm:flex-row sm:flex-wrap sm:items-center sm:gap-3"
			data-testid="revision-row"
			data-revision-id={revision.revisionId}
			// Cross-link jump target: receives focus from the Overlays section's
			// "revision …" links so keyboard/SR users land here perceivably.
			tabIndex={-1}
		>
			<div className="min-w-0 sm:flex-1">
				<div className="flex flex-wrap items-center gap-2">
					<Badge variant={stateVariant(revision.state)}>
						{revisionStateLabel(revision.state)}
					</Badge>
					{revision.isCurrent ? <Badge variant="success">Live</Badge> : null}
					<span
						className="text-muted-foreground truncate font-mono text-xs"
						title={revision.revisionId}
					>
						{shortId}
						<span className="sr-only"> (full id {revision.revisionId})</span>
					</span>
					<CopyButton
						value={revision.revisionId}
						size="icon"
						variant="ghost"
						ariaLabel={`Copy full id of revision ${shortId}`}
						toastMessage="Revision id copied"
					/>
					{producedBy ? (
						<Button
							variant="ghost"
							size="sm"
							onClick={() => {
								if (!jumpToOverlay(producedBy.id)) {
									toast({
										variant: 'default',
										title: 'Overlay not shown in the list below',
									});
								}
							}}
							aria-label={`Jump to overlay ${shortOverlayId(producedBy.id)}, which produced this revision`}
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
			<div className="flex flex-wrap gap-2 sm:shrink-0">
				<Button
					variant="ghost"
					size="sm"
					onClick={() => onViewSpec(revision)}
					aria-label={`${hasDiffBase ? 'Diff' : 'View spec of'} revision ${shortId}`}
					data-testid="revision-view-spec"
				>
					{hasDiffBase ? (
						<>
							<FileDiff size={14} aria-hidden="true" />
							Diff
						</>
					) : (
						<>
							<FileJson size={14} aria-hidden="true" />
							View spec
						</>
					)}
				</Button>
				{revision.promoteHref ? (
					<Button
						variant="secondary"
						size="sm"
						onClick={() => onPromote(revision.revisionId)}
						loading={pendingAction === 'promote'}
						aria-label={`Promote revision ${shortId}`}
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
						aria-label={`Archive revision ${shortId}`}
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
	// Shared cache with OverlaysSection — used to cross-link overlay-origin
	// revisions to the overlay that produced them. Best-effort: while loading
	// (or on error) rows simply render the plain origin label.
	const overlaysQuery = useOverlays(apiKey);
	const { promote, archive, pendingRevisionId, pendingAction } = useRevisionActions(apiKey);
	const [specRevision, setSpecRevision] = useState<ApiRevision | null>(null);

	const revisions = query.items;
	const overlays = overlaysQuery.items;

	return (
		<Card data-testid="revisions-section">
			<CardHeader>
				<CardTitle>Revisions</CardTitle>
				<p className="text-muted-foreground mt-0.5 text-xs">
					Every version of this API&apos;s spec, newest first. Imports, uploads, and
					applied overlays each create one; exactly one can be live (serving traffic).
				</p>
			</CardHeader>
			<CardBody>
				{query.isLoading ? (
					<SkeletonRows rows={3} />
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
						description="Import a spec to create this API's first revision."
					/>
				) : (
					<ul className="divide-border/60">
						{revisions.map((rev, index) => (
							<RevisionRow
								key={rev.revisionId}
								revision={rev}
								previous={revisions[index + 1] ?? null}
								producedBy={overlayForRevision(overlays, rev.revisionId)}
								hasDiffBase={diffBaseFor(rev, revisions) !== null}
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
						? `${revisionStateLabel(specRevision.state)}${specRevision.isCurrent ? ' · live' : ''} · ${shortRevisionId(specRevision.revisionId)}`
						: undefined
				}
				diffAgainst={specRevision ? diffBaseFor(specRevision, revisions) : null}
			/>
		</Card>
	);
}
