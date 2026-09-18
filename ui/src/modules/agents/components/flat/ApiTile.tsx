/**
 * ApiTile — one API on the flat Agents surface.
 *
 * The tile is the API; the credential that serves it is a property line on
 * the tile (underneath, it is still a binding). Reading top to bottom: the
 * vendor mark and the API's own identity (`name`, then `host · version`), a
 * hairline, then what the access is — a status chip with the auth type and
 * operation count beside it, the credential's name, and how that credential is
 * scoped (its rule count).
 *
 * States, which must never render identically:
 *   - Serving: solid border, a quiet `Ready` chip.
 *   - Suspended: the chip reads `Suspended` and a line says what that means
 *     (not serving calls until resumed) — pausing is reversible, so nothing
 *     here shouts.
 *   - Not usable yet: the credential exists but cannot serve traffic — an
 *     OAuth sign-in that hasn't completed. Dashed amber border, the reason
 *     named, and a "Finish connecting" affordance.
 *
 * Two explicit actions ride in the top-right — pause/resume this binding, and
 * open its access panel — because nothing else on a tile says that it is
 * interactive. They sit above the stretched overlay (`relative z-10`) that
 * makes the whole tile open the same panel for pointer users.
 *
 * Honesty contract: every fact is derived from reads the surface already holds
 * — never a per-tile fetch — and an unprovable fact is omitted rather than
 * guessed. No invented health, no green "ok"; tints flag only trouble (the
 * zero-rules grant, a pending sign-in).
 */
import { AlertTriangle, PauseCircle, PlayCircle, Settings2 } from 'lucide-react';
import { Badge, Button, Card, Tooltip, VendorIcon } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import type { BindingRuleSummary } from '@/modules/agents/api';
import type { ApiTileModel } from '@/modules/agents/lib/apiTiles';

interface ApiTileProps {
	tile: ApiTileModel;
	/** Effect breakdown of the operator rules on the tile's binding;
	 * undefined while unknown (loading or a failed read) — the summary line
	 * is omitted rather than guessed. */
	rules: BindingRuleSummary | undefined;
	/** Open the access sidebar for this tile's binding. */
	onOpen: () => void;
	/** Pause this binding (reversible — rules survive). */
	onSuspend: () => void;
	/** Lift a suspension on this binding. */
	onResume: () => void;
	/** A suspend/resume on THIS tile's credential is in flight. */
	bindingPending: boolean;
	/** Whether the access sidebar is currently open for this tile. */
	expanded: boolean;
	/** DOM id of the sidebar panel this tile controls (aria-controls). */
	sidebarId: string;
}

/** The grant-summary line under the credential name. A deny split rides
 * along when one exists — an all-allow grant stays the plain count. */
function grantSummary(rules: BindingRuleSummary | undefined): string | null {
	if (rules === undefined) return null;
	if (rules.total === 0) return 'No rules — all calls blocked';
	const count = rules.total === 1 ? '1 access rule' : `${rules.total} access rules`;
	return rules.deny > 0 ? `${count} · ${rules.deny} deny` : count;
}

export function ApiTile({
	tile,
	rules,
	onOpen,
	onSuspend,
	onResume,
	bindingPending,
	expanded,
	sidebarId,
}: ApiTileProps) {
	const summary = grantSummary(rules);
	// The registry's identity pair. The host is dropped when it repeats the
	// title (an API that isn't imported here knows no separate domain).
	const identity = [
		tile.host === tile.title ? null : tile.host,
		tile.version && `v${tile.version}`,
	]
		.filter(Boolean)
		.join(' · ');
	// What the access IS, beside the status chip.
	const capability = [
		tile.authLabel,
		tile.operationCount != null ? `${tile.operationCount.toLocaleString()} operations` : null,
	]
		.filter(Boolean)
		.join(' · ');

	return (
		<Card
			data-testid="api-tile"
			data-not-usable={tile.awaitingConsent || undefined}
			className={cn(
				'focus-within:ring-ring/50 hover:border-primary/50 relative flex h-full flex-col gap-3 p-4 transition-colors focus-within:ring-2',
				tile.awaitingConsent && 'border-warning/60 border-dashed',
			)}
		>
			{/* Stretched overlay: the whole tile opens the sidebar. */}
			<button
				type="button"
				className="absolute inset-0 cursor-pointer rounded-xl focus:outline-none"
				aria-haspopup="dialog"
				aria-expanded={expanded}
				aria-controls={expanded ? sidebarId : undefined}
				onClick={onOpen}
			>
				<span className="sr-only">{tile.title} — open access details</span>
			</button>
			<div className="flex items-start gap-3">
				<VendorIcon name={tile.title} vendor={tile.vendor} iconUrl={tile.iconUrl} />
				<div className="min-w-0 flex-1">
					<h3 className="truncate text-sm font-semibold">{tile.title}</h3>
					{identity && (
						<p className="text-muted-foreground truncate text-xs">{identity}</p>
					)}
				</div>
				{/* Above the overlay, so these verbs are reachable — and so the
				    tile advertises that it can be acted on at all. */}
				<div className="relative z-10 flex shrink-0 items-center gap-0.5">
					{tile.suspended ? (
						<Tooltip
							content="Resume this binding — rules survived; access is restored."
							interactiveChild
						>
							<Button
								variant="ghost"
								size="sm"
								loading={bindingPending}
								onClick={onResume}
								aria-label={`Resume ${tile.title} access`}
								className="text-muted-foreground hover:text-foreground px-1.5"
							>
								<PlayCircle className="h-4 w-4" />
							</Button>
						</Tooltip>
					) : (
						<Tooltip
							content="Pause this binding — reversible; rules survive and resume restores access."
							interactiveChild
						>
							<Button
								variant="ghost"
								size="sm"
								loading={bindingPending}
								onClick={onSuspend}
								aria-label={`Pause ${tile.title} access`}
								className="text-muted-foreground hover:text-foreground px-1.5"
							>
								<PauseCircle className="h-4 w-4" />
							</Button>
						</Tooltip>
					)}
					<Tooltip
						content="Manage access — credential, rules and tester."
						interactiveChild
					>
						<Button
							variant="ghost"
							size="sm"
							onClick={onOpen}
							aria-label={`Manage ${tile.title} access`}
							className="text-muted-foreground hover:text-foreground px-1.5"
						>
							<Settings2 className="h-4 w-4" />
						</Button>
					</Tooltip>
				</div>
			</div>

			<div className="border-border/60 mt-auto space-y-1.5 border-t pt-3">
				<div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
					{tile.awaitingConsent ? (
						<Badge variant="warning" dot>
							Sign-in needed
						</Badge>
					) : tile.suspended ? (
						// Muted, not tinted: a pause is a state, not a fault.
						<Badge className="bg-muted text-muted-foreground border-border" dot>
							Suspended
						</Badge>
					) : (
						<Badge variant="success" dot>
							Ready
						</Badge>
					)}
					{capability && (
						<span className="text-muted-foreground truncate text-xs">{capability}</span>
					)}
				</div>
				<p className="truncate text-sm font-medium">{tile.credentialName}</p>
				{tile.awaitingConsent ? (
					<div className="space-y-1.5">
						<p className="text-warning flex items-start gap-1.5 text-xs">
							<AlertTriangle
								className="mt-0.5 h-3.5 w-3.5 shrink-0"
								aria-hidden="true"
							/>
							<span>
								Waiting for a sign-in at {tile.vendor}. Calls through this API fail
								until the connection completes.
							</span>
						</p>
						<button
							type="button"
							className="text-primary relative z-10 cursor-pointer text-xs font-medium hover:underline"
							onClick={onOpen}
						>
							Finish connecting →
						</button>
					</div>
				) : (
					<>
						{summary && (
							<p
								className={cn(
									'text-xs',
									rules?.total === 0 ? 'text-warning' : 'text-muted-foreground',
								)}
							>
								{summary}
							</p>
						)}
						{tile.suspended && (
							<p className="text-muted-foreground text-xs">
								Binding suspended — not serving calls until resumed.
							</p>
						)}
					</>
				)}
			</div>
		</Card>
	);
}
