/**
 * OverlaysSection — an API's overlay lifecycle (list + status).
 *
 * jentic-one lets an operator layer an overlay on top of an imported API's
 * spec: a `pending` overlay is a submitted-but-not-yet-applied change, a
 * `confirmed` overlay has been materialized onto the served spec, and a
 * `deprecated` overlay has been superseded (manually, by a re-import adopting
 * a fresh upstream spec, or by a rollback). This section lists overlays
 * newest-first and makes each one legible:
 *
 * - a human-readable summary of what the overlay DOES, derived from its
 *   document's actions (preferring the author's per-action descriptions);
 * - the real submitting principal (`created_by`, resolved via ActorLabel) plus
 *   any free-text attribution (`contributed_by`);
 * - a derived lifecycle state (pending / active / confirmed / rolled back /
 *   deprecated) distinct from origin — the wire `status` alone can't tell a
 *   rollback from a re-import deprecation;
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
	Skeleton,
	EmptyState,
	ErrorAlert,
	CopyButton,
	ActorLabel,
} from '@/shared/ui';
import { GitBranch, Layers } from 'lucide-react';
import { useState } from 'react';
import type { BadgeVariant } from '@/shared/ui';
import {
	useOverlays,
	useOverlayActions,
	overlayLifecycle,
	OVERLAY_LIFECYCLE_LABEL,
	shortOverlayId,
	shortRevisionId,
	summarizeOverlayActions,
} from '@/modules/workspace/api';
import type { ApiKey, Overlay, OverlayLifecycle } from '@/modules/workspace/api';
import { ConfirmDialog } from '@/modules/workspace/components/ConfirmDialog';

/** Badge colour per derived lifecycle state. */
const LIFECYCLE_VARIANT: Record<OverlayLifecycle, BadgeVariant> = {
	pending: 'pending',
	active: 'success',
	confirmed: 'default',
	'rolled-back': 'warning',
	deprecated: 'default',
};

/** Absolute local datetime for lifecycle notes (e.g. "28 Jul 2026, 13:02"). */
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

/** Scroll a revision's row into view (rows carry `data-revision-id`). */
function scrollToRevision(revisionId: string) {
	document
		.querySelector(`[data-revision-id="${CSS.escape(revisionId)}"]`)
		?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

/** The dated note describing where the overlay is in its lifecycle. */
function lifecycleNote(overlay: Overlay, lifecycle: OverlayLifecycle): string | null {
	switch (lifecycle) {
		case 'rolled-back':
			return overlay.deprecatedAt
				? `Rolled back ${formatDateTime(overlay.deprecatedAt)}${
						overlay.supersededRevisionId
							? ` — revision ${shortRevisionId(overlay.supersededRevisionId)} restored`
							: ''
					}`
				: null;
		case 'deprecated':
			// A deprecated overlay that HAD materialized was superseded (typically
			// by a re-import adopting a fresh upstream spec); one that never
			// materialized was simply retired.
			return overlay.deprecatedAt
				? `${overlay.confirmedAt ? 'Superseded' : 'Deprecated'} ${formatDateTime(overlay.deprecatedAt)}`
				: null;
		case 'active':
		case 'confirmed':
			return overlay.confirmedAt ? `Confirmed ${formatDateTime(overlay.confirmedAt)}` : null;
		case 'pending':
			return `Submitted ${formatDateTime(overlay.createdAt)}`;
	}
}

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
	const note = lifecycleNote(overlay, lifecycle);

	return (
		<li
			className="border-border/60 flex flex-wrap items-center gap-3 border-b py-3 last:border-b-0"
			data-testid="overlay-row"
			data-overlay-id={overlay.id}
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
						{shortOverlayId(overlay.id)}
					</span>
					<CopyButton
						value={overlay.id}
						size="icon"
						variant="ghost"
						ariaLabel="Copy full overlay id"
						toastMessage="Overlay id copied"
					/>
					{overlay.confirmedRevisionId ? (
						<Button
							variant="ghost"
							size="sm"
							onClick={() => scrollToRevision(overlay.confirmedRevisionId as string)}
							title="Jump to the revision this overlay produced"
							data-testid="overlay-produced-revision"
						>
							<GitBranch size={12} aria-hidden="true" />
							revision {shortRevisionId(overlay.confirmedRevisionId)}
						</Button>
					) : null}
				</div>
				{summary.length > 0 ? (
					<ul className="mt-1 space-y-0.5" data-testid="overlay-summary">
						{summary.map((line, i) => (
							<li key={i} className="text-foreground/90 text-xs">
								{line}
							</li>
						))}
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
					{note ? ` · ${note}` : ''}
				</p>
			</div>
			<div className="flex shrink-0 gap-2">
				{overlay.confirmHref ? (
					<Button
						variant="secondary"
						size="sm"
						onClick={() => onConfirm(overlay.id)}
						loading={pendingAction === 'confirm'}
						data-testid="overlay-confirm"
					>
						Confirm
					</Button>
				) : null}
				{overlay.rollbackHref ? (
					<Button
						variant="ghost"
						size="sm"
						onClick={() => onRollback(overlay)}
						loading={pendingAction === 'rollback'}
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

	const overlays = query.data?.items ?? [];

	return (
		<Card data-testid="overlays-section">
			<CardHeader>
				<CardTitle>Overlays</CardTitle>
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
							query.error instanceof Error ? query.error : 'Failed to load overlays.'
						}
					/>
				) : overlays.length === 0 ? (
					<EmptyState
						icon={<Layers size={28} aria-hidden="true" />}
						title="No overlays"
						description="No overlays for this API."
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
