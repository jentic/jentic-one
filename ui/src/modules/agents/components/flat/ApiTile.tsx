/**
 * ApiTile — one API on the flat Agents surface; the credential serving it is a
 * labelled entry in the tile's footer, not a card of its own.
 *
 * A dashed border means no calls are flowing, and its tint says why: amber for an
 * unfinished sign-in on this API, neutral when the agent above it is not serving.
 * A suspension outranks the agent-level state.
 */
import type { ReactNode } from 'react';
import { KeyRound, PauseCircle, PlayCircle, Settings2 } from 'lucide-react';
import { Button, Card, StatusText, Tag, Tooltip, VendorIcon } from '@/shared/ui';
import { formatApiVersion } from '@/shared/lib';
import { cn, timeAgo } from '@/shared/lib/utils';
import type { BindingRuleSummary } from '@/modules/agents/api';
import { idTail } from '@/shared/credentials/lib/credentialIdentity';
import type { ApiTileModel } from '@/modules/agents/lib/apiTiles';
import { multiCredentialExplanation } from '@/modules/agents/components/flat/MultiCredentialNote';

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
	/** Set when the agent reaches this API through several credentials: the label
	 * that tells this tile's credential apart, always printed. */
	accountLabel?: string;
	/** How many of the agent's bindings serve this tile's API. Above 1 the header
	 * carries a credentials chip whose tooltip says how a call picks one. */
	accountCount?: number;
}

/** The grant summary after the credential in the footer. A deny split rides
 * along when one exists — an all-allow grant stays the plain count. A binding
 * governed by a shared rule set is summarised from the set, named first. */
function grantSummary(rules: BindingRuleSummary | undefined): string | null {
	if (rules === undefined) return null;
	const prefix = rules.ruleSet ? `Rule set ${rules.ruleSet.name} · ` : '';
	if (rules.total === 0) return `${prefix}No rules — all calls blocked`;
	const count = rules.total === 1 ? '1 access rule' : `${rules.total} access rules`;
	return `${prefix}${rules.deny > 0 ? `${count} · ${rules.deny} deny` : count}`;
}

/** Are these two strings the same identity once separators and case are set
 * aside? `slack.com` and `Slack.Com` are; `Slack` and `slack.com` are not. */
function sameIdentity(a: string, b: string): boolean {
	const key = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '');
	return key(a) === key(b);
}

/** "4d ago" from the shared compact age; "just now" under a second. */
function ago(iso: string): string {
	const age = timeAgo(iso);
	return age === 'now' ? 'just now' : `${age} ago`;
}

/** The credential line's hover/focus details — only facts the tile already holds;
 * a missing one is left out rather than shown as a dash. */
function CredentialDetails({ tile, name }: { tile: ApiTileModel; name: string }) {
	const scope = [
		tile.vendor,
		tile.apiName ?? 'all APIs',
		tile.version ? formatApiVersion(tile.version) : null,
	]
		.filter(Boolean)
		.join(' / ');
	// Each row is one text run ("Name: …"): the tooltip's description stays
	// mounted while closed, so a bare value would repeat the tile's own text.
	const rows: Array<[string, ReactNode]> = [
		['Name', name],
		['Auth', tile.authLabel],
		[
			'ID',
			<span key="id" className="font-mono">
				{`…${idTail(tile.credentialId)}`}
			</span>,
		],
		['Scope', scope],
		['Added', tile.credentialCreatedAt ? ago(tile.credentialCreatedAt) : null],
		['Bound', ago(tile.boundAt)],
	];
	return (
		<span className="block" data-testid="tile-credential-details">
			{rows
				.filter(([, value]) => value)
				.map(([label, value]) => (
					<span key={label} className="block break-words not-first:mt-0.5">
						{label}: {value}
					</span>
				))}
		</span>
	);
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
	accountLabel,
	accountCount = 1,
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
	// Every tile names its credential, labelled, in its footer. With several
	// credentials for one API the account label (which may carry an id tail) says
	// which one this tile is.
	const credentialLabel = (accountLabel ?? tile.credentialName).trim() || null;
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
					<div className="flex min-w-0 items-center gap-1.5">
						<h3 className="truncate text-sm font-semibold">{tile.title}</h3>
						{accountCount > 1 && (
							// Above the overlay, so hover and focus reach the tooltip.
							<Tooltip
								content={multiCredentialExplanation(tile.title, accountCount)}
								className="relative z-10 shrink-0 rounded-md"
								bubbleClassName="max-w-xs"
							>
								<Tag icon={KeyRound} data-testid="tile-accounts-badge">
									{accountCount} credentials
								</Tag>
							</Tooltip>
						)}
					</div>
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
						<StatusText tone="warning" data-testid="tile-status-chip">
							Sign-in needed
						</StatusText>
					) : tile.suspended ? (
						// Muted, not tinted: a pause is a state, not a fault, and it ranks
						// above the agent's own state.
						<StatusText tone="muted" data-testid="tile-status-chip">
							Suspended · not serving
						</StatusText>
					) : !agentServing ? (
						// A green `Ready` on an agent that serves nothing is the one claim
						// this tile must never make.
						<StatusText tone="muted" data-testid="tile-status-chip">
							Not serving
						</StatusText>
					) : (
						<StatusText tone="success" data-testid="tile-status-chip">
							Ready
						</StatusText>
					)}
					{capability && (
						<span className="text-muted-foreground truncate text-xs">{capability}</span>
					)}
				</div>
				{/* A fixed ONE-line detail slot: grid rows size to their tallest cell, so an
				    extra line here would stretch every tile beside it. */}
				<div
					className="flex h-[1.125rem] min-w-0 items-center gap-1.5 text-xs"
					data-testid="tile-detail-slot"
				>
					{tile.awaitingConsent ? (
						<>
							<span className="text-warning truncate">
								Sign-in at {tile.vendor} unfinished
							</span>
							<button
								type="button"
								className="text-primary relative z-10 w-fit shrink-0 cursor-pointer font-medium hover:underline"
								onClick={onOpen}
							>
								Finish connecting →
							</button>
						</>
					) : (
						<>
							{credentialLabel ? (
								// Above the overlay, so hover and focus reach the details. It
								// gives way first: the name truncates before the rules do.
								<Tooltip
									content={
										<CredentialDetails tile={tile} name={credentialLabel} />
									}
									className="relative z-10 min-w-0 shrink-[1000] items-center rounded-sm"
									bubbleClassName="max-w-xs"
								>
									<span
										className="flex min-w-0 items-center gap-1"
										data-testid="tile-credential"
									>
										<KeyRound
											aria-hidden="true"
											className="text-muted-foreground h-3 w-3 shrink-0"
										/>
										<span className="text-muted-foreground shrink-0">
											Credential<span className="sr-only">: </span>
										</span>
										<span
											className="text-foreground min-w-0 truncate font-medium"
											data-testid="tile-credential-label"
										>
											{credentialLabel}
										</span>
									</span>
								</Tooltip>
							) : (
								<span
									className="text-muted-foreground flex shrink-0 items-center gap-1"
									data-testid="tile-credential"
								>
									<KeyRound aria-hidden="true" className="h-3 w-3 shrink-0" />
									No credential
								</span>
							)}
							{summary && (
								<>
									<span
										aria-hidden="true"
										className="text-muted-foreground shrink-0"
									>
										·
									</span>
									<span
										data-testid="tile-rules-summary"
										className={cn(
											'min-w-0 truncate',
											rules?.total === 0
												? 'text-warning'
												: 'text-muted-foreground',
										)}
									>
										{summary}
									</span>
								</>
							)}
						</>
					)}
				</div>
			</div>
		</Card>
	);
}
