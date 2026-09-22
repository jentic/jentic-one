/**
 * ApiTile — one API on the flat Agents surface; the credential serving it is a
 * property line on the tile, not a card of its own.
 *
 * A dashed border means no calls are flowing, and its tint says why: amber for an
 * unfinished sign-in on this API, neutral when the agent above it is not serving.
 * A suspension outranks the agent-level state.
 */
import { PauseCircle, PlayCircle, Settings2 } from 'lucide-react';
import { Badge, Button, Card, Tooltip, VendorIcon } from '@/shared/ui';
import { formatApiVersion } from '@/shared/lib';
import { cn } from '@/shared/lib/utils';
import type { BindingRuleSummary } from '@/modules/agents/api';
import type { ApiTileModel } from '@/modules/agents/lib/apiTiles';

interface ApiTileProps {
	tile: ApiTileModel;
	/** Effect breakdown of the operator rules on the tile's binding; undefined while
	 * unknown, where the summary line is omitted rather than guessed. */
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
	// The host is dropped when the title is only a humanisation of it (`slack.com` →
	// `Slack.Com`), which would stack the same word twice.
	const identity = [
		sameIdentity(tile.host, tile.title) ? null : tile.host,
		formatApiVersion(tile.version),
	]
		.filter(Boolean)
		.join(' · ');
	// The credential is named only when it adds a fact, measured against BOTH names
	// the tile prints — a generically-titled spec can repeat the host.
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
				// Neutral dash: the tile is intact and editable, it just isn't carrying
				// calls. An unfinished sign-in tints the dash amber instead.
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
					{/* Exactly one chip, in precedence order: an unfinished sign-in outranks a
					    pause, which outranks the agent's own state. */}
					{tile.awaitingConsent ? (
						<Badge variant="warning" dot data-testid="tile-status-chip">
							Sign-in needed
						</Badge>
					) : tile.suspended ? (
						// Muted, not tinted: a pause is a state, not a fault, and it ranks
						// above the agent's own state.
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
				{/* A fixed ONE-line detail slot: grid rows size to their tallest cell, so an
				    extra line here would stretch every tile beside it. */}
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
