/**
 * ApiTile — one API on the flat Agents surface.
 *
 * The tile is the API; the credential that serves it is a property line on
 * the tile (underneath, it is still a binding). Reading top to bottom: the
 * vendor mark and the API's own identity (`name`, then `host · version`), a
 * hairline, then what the access is — a status chip with the auth type and
 * operation count beside it, and one meta line naming the credential and how it
 * is scoped (its rule count).
 *
 * Each identity is said once. The tile states the API twice at most — as the
 * title, and as the host when that is a different string — so the meta line
 * drops a credential whose name echoes either of them, and the `host` line
 * drops a domain the title is only a humanisation of.
 *
 * Every tile is the same height whatever its state: the meta line is a slot of
 * fixed height (one row), because a grid row sizes to its tallest cell and a
 * state that costs an extra line would otherwise stretch every tile beside it.
 * Each state chip therefore carries its own consequence in its own text, rather
 * than a row the other tiles reserve empty.
 *
 * A DASHED border is the family-wide mark of "no calls are flowing through this
 * tile", and its tint says whose fault that is: amber when this API itself wants
 * attention (an unfinished sign-in), neutral when the agent above it simply is
 * not serving. That is what makes a non-active agent's grid read as inactive at
 * a glance rather than as a normal grid under a notice.
 *
 * States, which must never render identically:
 *   - Serving: solid border, a quiet `Ready` chip.
 *   - The agent is not serving: neutral dashed border, a muted `Not serving`
 *     chip in place of `Ready` — every control stays live, because a
 *     non-serving agent is still fully editable.
 *   - Suspended: the chip reads `Suspended · not serving` — pausing is
 *     reversible, so nothing here shouts. It outranks the agent-level state:
 *     resuming this binding is a separate thing left to do.
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
import { PauseCircle, PlayCircle, Settings2 } from 'lucide-react';
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
	/** Whether the AGENT this tile belongs to is serving traffic. False for
	 * every non-active state, which the whole grid then reads as. */
	agentServing: boolean;
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

/** Are these two strings the same identity once separators and case are set
 * aside? `slack.com` and `Slack.Com` are; `Slack` and `slack.com` are not. */
function sameIdentity(a: string, b: string): boolean {
	const key = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '');
	return key(a) === key(b);
}

export function ApiTile({
	tile,
	rules,
	onOpen,
	onSuspend,
	onResume,
	bindingPending,
	agentServing,
	expanded,
	sidebarId,
}: ApiTileProps) {
	const summary = grantSummary(rules);
	// The registry's identity pair. The host is dropped when the title is only a
	// humanisation of it (`slack.com` → `Slack.Com`), which is what an API with
	// no separate domain and no friendly name resolves to — printing both would
	// stack the same word twice.
	const identity = [
		sameIdentity(tile.host, tile.title) ? null : tile.host,
		tile.version && `v${tile.version}`,
	]
		.filter(Boolean)
		.join(' · ');
	// The credential is named only when it adds a fact. It is measured against
	// BOTH names the tile prints, because a generically-named spec makes them
	// different strings: a secret called `PredictHQ` repeats the title, and one
	// called `aboutwayfair.com` repeats the host line of a tile titled `Openapi`.
	// Either way it is the API's own identity said twice.
	const credentialLabel =
		sameIdentity(tile.credentialName, tile.title) ||
		sameIdentity(tile.credentialName, tile.host)
			? null
			: tile.credentialName;
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
			data-not-serving={!agentServing || undefined}
			className={cn(
				'focus-within:ring-ring/50 hover:border-primary/50 relative flex h-full flex-col gap-3 p-4 transition-colors focus-within:ring-2',
				// Neutral dash + a recessed surface: the tile is intact and
				// editable, it just isn't carrying calls. An unfinished sign-in
				// still tints the dash amber — that one is asking for something.
				!agentServing && 'border-border bg-muted/25 border-dashed',
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
					{/* Reserved whether or not the registry proves an identity pair,
					    so an API without one doesn't sit shorter than its neighbours. */}
					<p className="text-muted-foreground h-[1.125rem] truncate text-xs">
						{identity}
					</p>
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
				<div className="flex items-center justify-between gap-2">
					{/* Exactly one chip, in precedence order: an unfinished sign-in
					    outranks a pause, which outranks the agent's own state. Each chip
					    carries its own consequence, so no state costs a line of its own
					    and no tile pays height for a state it isn't in. */}
					{tile.awaitingConsent ? (
						<Badge variant="warning" dot data-testid="tile-status-chip">
							Sign-in needed
						</Badge>
					) : tile.suspended ? (
						// Muted, not tinted: a pause is a state, not a fault. It ranks
						// above the agent's own state — resuming this binding is its own
						// outstanding thing to do.
						<Badge
							className="bg-muted text-muted-foreground border-border"
							dot
							data-testid="tile-status-chip"
						>
							Suspended · not serving
						</Badge>
					) : !agentServing ? (
						// A green `Ready` on an agent that serves nothing is the one claim
						// this tile must never make.
						<Badge
							className="bg-muted text-muted-foreground border-border"
							dot
							data-testid="tile-status-chip"
						>
							Not serving
						</Badge>
					) : (
						<Badge variant="success" dot data-testid="tile-status-chip">
							Ready
						</Badge>
					)}
					{capability && (
						<span className="text-muted-foreground truncate text-xs">{capability}</span>
					)}
				</div>
				{/* A fixed ONE-line detail slot. A tile's height must not depend on
				    its state — grid rows size to their tallest cell, so a state that
				    costs an extra line would stretch every tile beside it. The line
				    is reserved whether or not it is filled, and every branch renders
				    a single row. */}
				<div
					className="flex h-[1.125rem] items-center gap-1.5 overflow-hidden"
					data-testid="tile-detail-slot"
				>
					{tile.awaitingConsent ? (
						<>
							<span className="text-warning truncate text-xs">
								Sign-in at {tile.vendor} unfinished
							</span>
							<button
								type="button"
								className="text-primary relative z-10 w-fit shrink-0 cursor-pointer text-xs font-medium hover:underline"
								onClick={onOpen}
							>
								Finish connecting →
							</button>
						</>
					) : (
						<>
							{credentialLabel && (
								<span className="shrink-0 text-xs font-medium">
									{credentialLabel}
								</span>
							)}
							{credentialLabel && summary && (
								<span className="text-muted-foreground text-xs">·</span>
							)}
							{summary && (
								<span
									className={cn(
										'truncate text-xs',
										rules?.total === 0
											? 'text-warning'
											: 'text-muted-foreground',
									)}
								>
									{summary}
								</span>
							)}
						</>
					)}
				</div>
			</div>
		</Card>
	);
}
