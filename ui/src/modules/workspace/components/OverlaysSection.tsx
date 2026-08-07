/**
 * OverlaysSection — an API's overlay lifecycle (list + status).
 *
 * jentic-one lets an operator layer an overlay on top of an imported API's
 * spec: a `pending` overlay is a submitted-but-not-yet-applied change, a
 * `confirmed` overlay has been materialized onto the served spec, and a
 * `deprecated` overlay has been retired (manually, by a re-import adopting
 * a fresh upstream spec, or by a rollback). This section lists overlays
 * newest-first and makes each one legible:
 *
 * - a human-readable summary of what the overlay DOES, derived from its
 *   document's actions (preferring the author's per-action descriptions),
 *   capped at a few lines so a many-action overlay can't drown the list;
 * - the real submitting principal (`created_by`, resolved via ActorLabel) plus
 *   any free-text attribution (`contributed_by`);
 * - a derived lifecycle state (pending / active / superseded / confirmed /
 *   rolled back / deprecated) distinct from origin — the wire `status` alone
 *   can't tell a rollback from a re-import deprecation, and a "deprecated"
 *   overlay whose revision still serves is called out explicitly;
 * - a unique short id (`ovr_…1840e8` — KSUIDs share their LEADING chars, so
 *   the tail is shown) with a copy-full-id affordance;
 * - a link to the revision the overlay produced (jumps to the Revisions row).
 *
 * Modeled on `RevisionsSection`: same Card scaffold, loading/error/empty
 * handling, and per-row status badge conventions.
 */
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
import { GitBranch, Layers } from 'lucide-react';
import { useState } from 'react';
import type { BadgeVariant } from '@/shared/ui';
import {
	useOverlays,
	useOverlayActions,
	overlayLifecycle,
	overlayLifecycleNote,
	OVERLAY_LIFECYCLE_LABEL,
	shortOverlayId,
	shortRevisionId,
	summarizeOverlayActions,
} from '@/modules/workspace/api';
import type { ApiKey, Overlay, OverlayLifecycle } from '@/modules/workspace/api';
import { ConfirmDialog } from '@/modules/workspace/components/ConfirmDialog';
import { jumpToRevision } from '@/modules/workspace/components/jumpToRow';

/**
 * Badge colour per derived lifecycle state. The two easiest-to-confuse states
 * get distinct variants: `superseded` (was applied, no longer serving) stays
 * neutral while `deprecated` (retired) reads as terminal.
 */
const LIFECYCLE_VARIANT: Record<OverlayLifecycle, BadgeVariant> = {
	pending: 'pending',
	active: 'success',
	superseded: 'default',
	confirmed: 'default',
	'rolled-back': 'warning',
	'deprecated-serving': 'warning',
	deprecated: 'danger',
};

/** Max summary lines rendered per row before collapsing to "+N more actions". */
const MAX_SUMMARY_LINES = 3;

type OverlayAction = 'confirm' | 'rollback' | 'deprecate' | null;

function OverlayRow({
	overlay,
	currentRevisionId,
	onConfirm,
	onDeprecate,
	onRollback,
	pendingAction,
}: {
	overlay: Overlay;
	/** The API's current revision — needed to derive the lifecycle state. */
	currentRevisionId: string | null;
	onConfirm: (id: string) => void;
	onDeprecate: (id: string) => void;
	/** Rollback is destructive (rewrites the served spec) — routed through a confirm dialog. */
	onRollback: (overlay: Overlay) => void;
	/** Which action (if any) is in flight for *this* row — spins only that button. */
	pendingAction: OverlayAction;
}) {
	const lifecycle = overlayLifecycle(overlay, currentRevisionId);
	const summary = summarizeOverlayActions(overlay.document);
	const shownSummary = summary.slice(0, MAX_SUMMARY_LINES);
	const hiddenActions = summary.length - shownSummary.length;
	const note = overlayLifecycleNote(overlay, lifecycle);
	const shortId = shortOverlayId(overlay.id);

	return (
		<li
			className="border-border/60 flex flex-wrap items-center gap-3 border-b py-3 last:border-b-0"
			data-testid="overlay-row"
			data-overlay-id={overlay.id}
			// Cross-link jump target: receives focus from the Revisions section's
			// "overlay …" origin links so keyboard/SR users land here perceivably.
			tabIndex={-1}
		>
			<div className="min-w-0 flex-1">
				<div className="flex flex-wrap items-center gap-2">
					<Badge
						variant={LIFECYCLE_VARIANT[lifecycle]}
						data-testid={`overlay-status-${overlay.status}`}
						data-lifecycle={lifecycle}
					>
						{OVERLAY_LIFECYCLE_LABEL[lifecycle]}
					</Badge>
					<span
						className="text-muted-foreground truncate font-mono text-xs"
						title={overlay.id}
						data-testid="overlay-id"
					>
						{shortId}
						<span className="sr-only"> (full id {overlay.id})</span>
					</span>
					<CopyButton
						value={overlay.id}
						size="icon"
						variant="ghost"
						ariaLabel={`Copy full id of overlay ${shortId}`}
						toastMessage="Overlay id copied"
					/>
					{overlay.confirmedRevisionId ? (
						<Button
							variant="ghost"
							size="sm"
							onClick={() => {
								if (!jumpToRevision(overlay.confirmedRevisionId as string)) {
									toast({
										variant: 'default',
										title: 'Revision not shown in the list above',
									});
								}
							}}
							aria-label={`Jump to revision ${shortRevisionId(overlay.confirmedRevisionId)}, which this overlay produced`}
							title="Jump to the revision this overlay produced"
							data-testid="overlay-produced-revision"
						>
							<GitBranch size={12} aria-hidden="true" />
							revision {shortRevisionId(overlay.confirmedRevisionId)}
						</Button>
					) : null}
				</div>
				{shownSummary.length > 0 ? (
					<ul className="mt-1 space-y-0.5" data-testid="overlay-summary">
						{shownSummary.map((line, i) => (
							<li key={i} className="text-foreground/90 text-xs">
								{line}
							</li>
						))}
						{hiddenActions > 0 ? (
							<li
								className="text-muted-foreground text-xs"
								data-testid="overlay-summary-more"
							>
								+{hiddenActions} more action{hiddenActions === 1 ? '' : 's'}
							</li>
						) : null}
					</ul>
				) : null}
				<p className="text-muted-foreground mt-1 text-xs" data-testid="overlay-meta">
					{overlay.createdBy ? (
						<>
							by <ActorLabel actorId={overlay.createdBy} />
						</>
					) : (
						'unknown author'
					)}
					{overlay.contributedBy ? ` · via ${overlay.contributedBy}` : ''}
					{` · ${note}`}
				</p>
			</div>
			<div className="flex shrink-0 gap-2">
				{overlay.confirmHref ? (
					<Button
						variant="secondary"
						size="sm"
						onClick={() => onConfirm(overlay.id)}
						loading={pendingAction === 'confirm'}
						aria-label={`Confirm overlay ${shortId}`}
						data-testid="overlay-confirm"
					>
						Confirm
					</Button>
				) : null}
				{/* The backend advertises `rollback` for any materialized overlay, but
				    the service 409s unless the overlay's revision is CURRENT — gate on
				    the derived lifecycle so we never render a guaranteed-to-fail button. */}
				{overlay.rollbackHref && lifecycle === 'active' ? (
					<Button
						variant="ghost"
						size="sm"
						onClick={() => onRollback(overlay)}
						loading={pendingAction === 'rollback'}
						aria-label={`Roll back overlay ${shortId}`}
						data-testid="overlay-rollback"
					>
						Roll back
					</Button>
				) : null}
				{overlay.deprecateHref ? (
					<Button
						variant="ghost"
						size="sm"
						onClick={() => onDeprecate(overlay.id)}
						loading={pendingAction === 'deprecate'}
						aria-label={`Deprecate overlay ${shortId}`}
						data-testid="overlay-deprecate"
					>
						Deprecate
					</Button>
				) : null}
			</div>
		</li>
	);
}

export function OverlaysSection({
	apiKey,
	currentRevisionId = null,
}: {
	apiKey: ApiKey;
	/** The API's current revision id — drives the derived lifecycle badges. */
	currentRevisionId?: string | null;
}) {
	const query = useOverlays(apiKey);
	const { confirm, rollback, deprecate, pendingOverlayId, pendingAction } =
		useOverlayActions(apiKey);
	// Rollback rewrites the served spec (restores the revision the overlay
	// superseded), so it is gated behind an explicit confirm step.
	const [rollbackTarget, setRollbackTarget] = useState<Overlay | null>(null);

	const overlays = query.items;

	return (
		<Card data-testid="overlays-section">
			<CardHeader>
				<CardTitle>Overlays</CardTitle>
			</CardHeader>
			<CardBody>
				{query.isLoading ? (
					<SkeletonRows rows={3} />
				) : query.isError ? (
					<ErrorAlert
						message={
							query.error instanceof Error ? query.error : 'Failed to load overlays.'
						}
					/>
				) : overlays.length === 0 ? (
					<EmptyState
						icon={<Layers size={28} aria-hidden="true" />}
						title="No overlays"
						description="Overlays are reviewed spec fixes submitted by agents or operators; they'll appear here once submitted."
					/>
				) : (
					<ul className="divide-border/60">
						{overlays.map((overlay) => (
							<OverlayRow
								key={overlay.id}
								overlay={overlay}
								currentRevisionId={currentRevisionId}
								onConfirm={confirm}
								onDeprecate={deprecate}
								onRollback={setRollbackTarget}
								pendingAction={
									pendingOverlayId === overlay.id ? pendingAction : null
								}
							/>
						))}
					</ul>
				)}
			</CardBody>
			<ConfirmDialog
				open={rollbackTarget != null}
				title="Roll back overlay?"
				body={
					<>
						Rolling back overlay{' '}
						<span className="font-mono">
							{rollbackTarget ? shortOverlayId(rollbackTarget.id) : ''}
						</span>{' '}
						restores the revision it superseded and rewrites the served spec. This can't
						be undone automatically.
					</>
				}
				confirmLabel="Roll back"
				confirmTestId="overlay-rollback-confirm"
				onConfirm={() => {
					if (rollbackTarget) rollback(rollbackTarget.id);
					setRollbackTarget(null);
				}}
				onClose={() => setRollbackTarget(null)}
			/>
		</Card>
	);
}
