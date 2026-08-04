/**
 * OverlaysSection — an API's overlay lifecycle (list + status).
 *
 * jentic-one lets an operator layer an overlay on top of an imported API's
 * spec: a `pending` overlay is a submitted-but-not-yet-applied change, a
 * `confirmed` overlay has been materialized onto the served spec, and a
 * `deprecated` overlay has been superseded (either manually or automatically by
 * a re-import adopting a fresh upstream spec — the "close the overlay-update
 * loop" flow). This section lists overlays newest-first, badges their status,
 * and — for a deprecated overlay — explains WHEN/WHY it was retired.
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
} from '@/shared/ui';
import { Layers } from 'lucide-react';
import { useState } from 'react';
import type { BadgeVariant } from '@/shared/ui';
import { useOverlays, useOverlayActions } from '@/modules/workspace/api';
import type { ApiKey, Overlay, OverlayStatus } from '@/modules/workspace/api';
import { ConfirmDialog } from '@/modules/workspace/components/ConfirmDialog';

/** Badge colour per known overlay status; unknown wire values fall back to `default`. */
const STATUS_VARIANT: Partial<Record<OverlayStatus, BadgeVariant>> = {
	pending: 'pending',
	confirmed: 'success',
	deprecated: 'warning',
};

function statusVariant(status: OverlayStatus): BadgeVariant {
	return STATUS_VARIANT[status] ?? 'default';
}

/** Absolute local datetime for the deprecation note (e.g. "28 Jul 2026, 13:02"). */
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

type OverlayAction = 'confirm' | 'rollback' | 'deprecate' | null;

function OverlayRow({
	overlay,
	onConfirm,
	onDeprecate,
	onRollback,
	pendingAction,
}: {
	overlay: Overlay;
	onConfirm: (id: string) => void;
	onDeprecate: (id: string) => void;
	/** Rollback is destructive (rewrites the served spec) — routed through a confirm dialog. */
	onRollback: (overlay: Overlay) => void;
	/** Which action (if any) is in flight for *this* row — spins only that button. */
	pendingAction: OverlayAction;
}) {
	return (
		<li
			className="border-border/60 flex flex-wrap items-center gap-3 border-b py-3 last:border-b-0"
			data-testid="overlay-row"
		>
			<div className="min-w-0 flex-1">
				<div className="flex items-center gap-2">
					<Badge
						variant={statusVariant(overlay.status)}
						data-testid={`overlay-status-${overlay.status}`}
					>
						{overlay.status}
					</Badge>
					<span className="text-muted-foreground truncate font-mono text-xs">
						{overlay.id.slice(0, 8)}
					</span>
				</div>
				<p className="text-muted-foreground mt-1 text-xs">
					{overlay.createdBy ? `by ${overlay.createdBy}` : 'unknown author'}
					{overlay.status === 'deprecated' && overlay.deprecatedAt
						? ` · Deprecated by re-import on ${formatDateTime(overlay.deprecatedAt)}`
						: ''}
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

export function OverlaysSection({ apiKey }: { apiKey: ApiKey }) {
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
						<span className="font-mono">{rollbackTarget?.id.slice(0, 8)}</span> restores
						the revision it superseded and rewrites the served spec. This can't be
						undone automatically.
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
