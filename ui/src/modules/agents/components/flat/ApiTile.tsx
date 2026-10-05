/**
 * ApiTile — one API on the flat Agents surface; the credential serving it is a
 * property line on the tile, not a card of its own.
 *
 * A borderless tonal card: identity on top, the facts (auth · operations, then
 * credential · rules) under it, and one status marker with the quiet tonal verbs
 * along the bottom. A tile that carries no calls — a paused binding, or an agent
 * that is not serving — dims and desaturates, and its marker says which; an
 * unfinished sign-in keeps full strength with a warning marker and the fix.
 * A suspension outranks the agent-level state.
 */
import { LogIn, PauseCircle, PlayCircle, Settings2 } from 'lucide-react';
import { Button, Card, StatusText, Tooltip, VendorIcon } from '@/shared/ui';
import { formatApiVersion, vendorIconPropsFor } from '@/shared/lib';
import { cn } from '@/shared/lib/utils';
import type { BindingRuleSummary, BindingRulesState } from '@/modules/agents/api';
import type { ApiTileModel } from '@/modules/agents/lib/apiTiles';
import { deriveTileStatus, ruleSummaryOf } from '@/modules/agents/lib/tileStatus';
import { TileStatusText } from '@/modules/agents/components/flat/TileStatusMarker';

interface ApiTileProps {
	tile: ApiTileModel;
	/** Effect breakdown of the operator rules on the tile's binding, or where its
	 * read stands — the summary line is omitted rather than guessed while unknown. */
	rules: BindingRulesState | undefined;
	/** Re-read the binding's rules after a failed read. */
	onRetryRules?: () => void;
	/** Open the access sidebar for this tile's binding. */
	onOpen: () => void;
	/** Open the access sidebar focused on its rules editor (the Blocked fix). */
	onOpenRules?: () => void;
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
	onRetryRules,
	onOpen,
	onOpenRules,
	onSuspend,
	onResume,
	bindingPending,
	agentServing,
	expanded,
	sidebarId,
}: ApiTileProps) {
	// The tile's ONE status, in precedence order — see `deriveTileStatus`.
	const status = deriveTileStatus({
		suspended: tile.suspended,
		agentServing,
		awaitingConsent: tile.awaitingConsent,
		rules,
	});
	// The status already says "Blocked · no rules", so the meta line doesn't
	// repeat it; elsewhere (e.g. a paused binding) it is the only place it shows.
	const summary = status === 'blocked-no-rules' ? null : grantSummary(ruleSummaryOf(rules));
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

	// What stops calls on this tile right now: a pause on the binding, or an agent
	// that serves nothing. Either way the card dims; the marker says which.
	const idle = tile.suspended || !agentServing;

	return (
		<Card
			data-testid="api-tile"
			data-not-usable={tile.awaitingConsent || undefined}
			data-not-serving={!agentServing || undefined}
			data-suspended={tile.suspended || undefined}
			selected={expanded}
			className={cn(
				'card-hover relative flex h-full flex-col gap-3 px-5 pt-5 pb-[18px]',
				'focus-within:shadow-[0_0_0_1.5px_hsl(var(--primary)/0.45)]',
				// Borderless, so "not carrying calls" can't be a dashed edge: the
				// surface steps back (dimmer, desaturated) and the marker names why.
				idle && 'bg-surface-1/55 hover:bg-surface-1/80',
			)}
		>
			{/* Stretched overlay: the whole tile opens the sidebar. */}
			<button
				type="button"
				className="absolute inset-0 cursor-pointer rounded-lg focus:outline-none"
				aria-haspopup="dialog"
				aria-expanded={expanded}
				aria-controls={expanded ? sidebarId : undefined}
				onClick={onOpen}
			>
				<span className="sr-only">{tile.title} — open access details</span>
			</button>
			<div className="grid grid-cols-[36px_minmax(0,1fr)] items-start gap-3">
				<VendorIcon
					{...vendorIconPropsFor(tile)}
					size="md"
					className={cn(idle && 'saturate-[.3]')}
				/>
				<div className="min-w-0">
					<h3
						className={cn(
							'font-heading mt-px truncate text-[14.5px] leading-[1.3] font-semibold',
							idle ? 'text-foreground-idle' : 'text-foreground-name',
						)}
					>
						{tile.title}
					</h3>
					{/* Reserved whether or not the registry proves an identity pair,
					    so an API without one doesn't sit shorter than its neighbours. */}
					<p className="text-foreground-sub mt-0.5 h-[1.125rem] truncate text-[12.5px] leading-[1.4]">
						{identity}
					</p>
					<p
						className="text-foreground-sub h-[1.125rem] truncate text-xs leading-[1.5]"
						data-testid="tile-capability"
					>
						{capability}
					</p>
					{/* A fixed ONE-line detail slot: grid rows size to their tallest cell,
					    so an extra line here would stretch every tile beside it. */}
					<div
						className="flex h-[1.125rem] items-center gap-1.5 overflow-hidden text-xs"
						data-testid="tile-detail-slot"
					>
						{tile.awaitingConsent ? (
							<StatusText
								tone="warning"
								size="sm"
								plain
								icon={LogIn}
								className="min-w-0 truncate"
							>
								<span className="truncate">
									Sign-in at {tile.vendor} unfinished
								</span>
							</StatusText>
						) : (
							<>
								{credentialLabel && (
									<span className="text-foreground-lighter shrink-0 font-semibold">
										{credentialLabel}
									</span>
								)}
								{credentialLabel && summary && (
									<span className="text-foreground-faint" aria-hidden="true">
										·
									</span>
								)}
								{summary && (
									<span
										className="text-foreground-sub truncate"
										data-testid="tile-rules-summary"
									>
										{summary}
									</span>
								)}
							</>
						)}
					</div>
				</div>
			</div>

			<div className="mt-auto flex items-center justify-end gap-1.5">
				{/* Exactly one marker (`deriveTileStatus`): suspended → not serving →
				    sign-in needed → checking / unavailable → blocked → ready. Blocked is
				    a button to the rules; unavailable carries a Retry. */}
				<span className="mr-auto min-w-0">
					<TileStatusText
						status={status}
						apiTitle={tile.title}
						onOpenRules={onOpenRules ?? onOpen}
						onRetry={onRetryRules}
					/>
				</span>
				{/* Above the overlay, so these verbs are reachable — and so the tile
				    advertises that it can be acted on at all. */}
				<div className="relative z-10 flex shrink-0 items-center gap-1.5">
					{tile.awaitingConsent && (
						<Button variant="tonal" size="xs" onClick={onOpen}>
							Finish connecting →
						</Button>
					)}
					{tile.suspended ? (
						<Tooltip
							content="Resume this binding — rules survived; access is restored."
							interactiveChild
						>
							<Button
								variant="tonal"
								size="icon-xs"
								loading={bindingPending}
								onClick={onResume}
								aria-label={`Resume ${tile.title} access`}
							>
								<PlayCircle className="h-3.5 w-3.5" />
							</Button>
						</Tooltip>
					) : (
						<Tooltip
							content="Pause this binding — reversible; rules survive and resume restores access."
							interactiveChild
						>
							<Button
								variant="tonal"
								size="icon-xs"
								loading={bindingPending}
								onClick={onSuspend}
								aria-label={`Pause ${tile.title} access`}
							>
								<PauseCircle className="h-3.5 w-3.5" />
							</Button>
						</Tooltip>
					)}
					<Tooltip
						content="Manage access — credential, rules and tester."
						interactiveChild
					>
						<Button
							variant="tonal"
							size="icon-xs"
							onClick={onOpen}
							aria-label={`Manage ${tile.title} access`}
						>
							<Settings2 className="h-3.5 w-3.5" />
						</Button>
					</Tooltip>
				</div>
			</div>
		</Card>
	);
}
