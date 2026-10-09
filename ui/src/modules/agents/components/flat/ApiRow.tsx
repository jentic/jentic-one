/**
 * ApiRow — one API the selected agent can call, as a row on the agent card's
 * "Can call" tree. The credential serving it is a labelled entry on the row,
 * not a card of its own.
 *
 * At rest the row is clean: identity, the facts (auth · operations, then the
 * credential), one 7-day call figure, one status marker, and its two controls
 * — Manage access (the access sheet) and the accordion chevron.
 *
 * It grows in place (`ApiRowReveal`) two ways:
 *  - PINNED — a click or tap on the row, or the chevron (Enter/Space on it:
 *    the chevron is the row's one keyboard control), pins it open (a
 *    previewed row stays open, now pinned); the same again on a pinned row
 *    unpins just this row. Pins are the list's (`pinned` /
 *    `onTogglePin`, from `usePinStack`): any number at once, so two
 *    credentials can sit side by side.
 *  - PREVIEW — a mouse at rest on an unpinned row (`useHoverIntent`, never
 *    while the list scrolls under a still cursor, nor over the row's controls),
 *    or keyboard focus inside it (its controls aside). Hover leaves pinned
 *    rows alone.
 * The chevron and its `aria-expanded` follow what is visibly open — a pin or
 * a preview.
 * Manage access, or the reveal's own, opens the sheet; the row goes back to
 * rest behind it (the list drops its pin). The reveal itself is not a target —
 * only its controls act. A row that carries no calls (a paused binding, or an
 * agent that is not serving) steps back, and its marker says which. A
 * suspension outranks the agent-level state.
 */
import { memo, useId, useRef, useState, type FocusEvent as ReactFocusEvent } from 'react';
import { useReducedMotionConfig } from 'framer-motion';
import { ChevronRight, KeyRound, Loader2, LogIn, PlayCircle, Settings2 } from 'lucide-react';
import {
	Button,
	ExpandReveal,
	InlineAction,
	REVEAL_MOTION,
	Skeleton,
	SparklineChart,
	StatusText,
	Tag,
	Tooltip,
	VendorIcon,
} from '@/shared/ui';
import { formatApiVersion, vendorIconPropsFor } from '@/shared/lib';
import { cn } from '@/shared/lib/utils';
import { HOVER_INTENT_IGNORE, useHoverIntent } from '@/shared/hooks';
import type { BindingRulesState } from '@/modules/agents/api';
import type { ApiTileModel } from '@/modules/agents/lib/apiTiles';
import {
	deriveTileStatus,
	isBlockedStatus,
	rulesSummaryText,
} from '@/modules/agents/lib/tileStatus';
import { TileStatusText } from '@/modules/agents/components/flat/TileStatusMarker';
import { multiCredentialExplanation } from '@/modules/agents/components/flat/MultiCredentialNote';
import { ApiRowReveal } from '@/modules/agents/components/flat/ApiRowReveal';
import {
	API_ROW_COLUMNS,
	API_ROW_COLUMNS_LG,
	API_ROW_GAP,
	API_ROW_INSET,
	API_ROW_METRIC_STATUS_LG,
} from '@/modules/agents/components/flat/apiRowGrid';
import type { ApiRowActivity } from '@/modules/agents/lib/apiRowActivity';

interface ApiRowProps {
	agentId: string;
	tile: ApiTileModel;
	/** Effect breakdown of the operator rules on the row's binding, or where its
	 * read stands — the summary is omitted rather than guessed while unknown. */
	rules: BindingRulesState | undefined;
	/** Re-read the binding's rules after a failed read. */
	onRetryRules?: () => void;
	/** The row's traffic (see `ApiRowActivity`). */
	activity: ApiRowActivity;
	/** Open the access sheet for this row's binding. */
	onOpen: () => void;
	/** Manage access (the row's icon button, and the reveal's): open the
	 * sheet the way this row needs it. Defaults to the rules editor for a
	 * Blocked row (`onOpenRules`), the plain sheet otherwise. */
	onManage?: () => void;
	/** Open the access sheet focused on its rules editor (the Blocked fix). */
	onOpenRules?: () => void;
	/** Pause this binding (reversible — rules survive). Omitted when the viewer
	 * may not manage the agent's bindings: the row then offers no pause. */
	onSuspend?: () => void;
	/** Lift a suspension on this binding. Omitted like `onSuspend`. */
	onResume?: () => void;
	/** Whether to offer "Finish connecting" for a binding awaiting sign-in. Off
	 * for a viewer who may not write credentials, matching the sidebar. */
	canConnect?: boolean;
	/** A suspend/resume on THIS row's credential is in flight. */
	bindingPending: boolean;
	/** Whether the AGENT this row belongs to is serving traffic. */
	agentServing: boolean;
	/** Whether the access sheet is currently open for this row. */
	expanded: boolean;
	/** DOM id of the sheet this row controls (aria-controls). */
	sidebarId: string;
	/** Set when the agent reaches this API through several credentials: the label
	 * that tells this row's credential apart, always printed. */
	accountLabel?: string;
	/** How many of the agent's bindings serve this row's API. */
	accountCount?: number;
	/** The list holds this row open (a pin). */
	pinned?: boolean;
	/** Pin or unpin this row — and only this row. */
	onTogglePin?: () => void;
}

/** Are these two strings the same identity once separators and case are set
 * aside? `slack.com` and `Slack.Com` are; `Slack` and `slack.com` are not. */
function sameIdentity(a: string, b: string): boolean {
	const key = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '');
	return key(a) === key(b);
}

/** Memoised: the list re-renders on every pin and peek, and a row only needs
 * to when its own props change (every callback it gets is stable per row). */
export const ApiRow = memo(function ApiRow({
	agentId,
	tile,
	rules,
	onRetryRules,
	activity,
	onOpen,
	onManage,
	onOpenRules,
	onSuspend,
	onResume,
	bindingPending,
	agentServing,
	expanded,
	sidebarId,
	accountLabel,
	accountCount = 1,
	pinned = false,
	onTogglePin,
	canConnect = true,
}: ApiRowProps) {
	const reducedMotion = useReducedMotionConfig();
	const status = deriveTileStatus({
		suspended: tile.suspended,
		agentServing,
		awaitingConsent: tile.awaitingConsent,
		rules,
	});
	// The marker already says "Blocked · no rules"; elsewhere the summary is the
	// only place the grant shows.
	const summary = status === 'blocked-no-rules' ? null : rulesSummaryText(rules);
	const identity = [
		sameIdentity(tile.host, tile.title) ? null : tile.host,
		formatApiVersion(tile.version),
	]
		.filter(Boolean)
		.join(' · ');
	const credentialLabel = (accountLabel ?? tile.credentialName).trim() || null;
	const capability = [
		tile.authLabel,
		tile.operationCount != null ? `${tile.operationCount.toLocaleString()} operations` : null,
	]
		.filter(Boolean)
		.join(' · ');
	const idle = tile.suspended || !agentServing;

	// Reveal: pinned by the list, or previewed — a mouse at rest on the row,
	// or keyboard focus anywhere in it. Pointer focus never reveals and blur
	// never collapses: focus wanders wherever a tap lands, and the sheet hands
	// it back when it closes.
	const hover = useHoverIntent<HTMLElement>({ pinned });
	const [keyboardFocused, setKeyboardFocused] = useState(false);
	/** The pointer behind a coming click on the row or chevron; null for a keyboard click. */
	const pressPointer = useRef<string | null>(null);
	/** Pinned by a tap: once grown, bring what it grew into view. */
	const scrollOnOpen = useRef(false);
	/** The sheet opened from here: the focus it hands back on close is not a
	 * reason to reveal. */
	const handBack = useRef(false);
	const manageRef = useRef<HTMLButtonElement>(null);
	const revealRef = useRef<HTMLDivElement>(null);
	const revealId = useId();
	const revealed = pinned || hover.open || keyboardFocused;

	function onFocus(event: ReactFocusEvent) {
		if (handBack.current) {
			handBack.current = false;
			return;
		}
		const target = event.target as Element;
		// The row's controls (Manage access, the chevron, a clickable status)
		// are their own: focus on them previews nothing.
		if (target.closest('[data-hover-intent="ignore"]')) return;
		if (target.matches(':focus-visible')) setKeyboardFocused(true);
	}
	/** Open the sheet and put the row back to rest behind it. Focus goes to
	 * the row's own Manage access first, so that is where the sheet hands it
	 * back. */
	function openSheet(open: () => void) {
		manageRef.current?.focus({ preventScroll: true });
		handBack.current = true;
		hover.close();
		setKeyboardFocused(false);
		open();
	}
	/** A press on the row or the chevron — mouse, tap, Enter or Space — pins
	 * this row, or unpins just this row. Unpinned, it folds: a keyboard
	 * preview goes with the pin, and a pointer on it keeps it shut. */
	function togglePin() {
		const pointer = pressPointer.current;
		pressPointer.current = null;
		if (pinned) {
			setKeyboardFocused(false);
			hover.close();
		} else {
			scrollOnOpen.current = pointer != null && pointer !== 'mouse';
		}
		onTogglePin?.();
	}
	const notePress = (event: { pointerType: string }) => {
		pressPointer.current = event.pointerType;
	};

	const usage = activity.usage;
	const blocked = isBlockedStatus(status);
	const manage = () => openSheet(onManage ?? (blocked ? (onOpenRules ?? onOpen) : onOpen));
	const manageLabel = credentialLabel
		? `Manage access for ${tile.title} (${credentialLabel})`
		: `Manage access for ${tile.title}`;

	return (
		<article
			data-testid="api-tile"
			data-revealed={revealed || undefined}
			data-pinned={pinned || undefined}
			data-not-usable={tile.awaitingConsent || undefined}
			data-not-serving={!agentServing || undefined}
			data-suspended={tile.suspended || undefined}
			data-status={status}
			ref={hover.ref}
			{...hover.handlers}
			onFocus={onFocus}
			onBlur={(event) => {
				if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
					setKeyboardFocused(false);
				}
			}}
			className={cn(
				'bg-surface-1 relative rounded-[14px] transition-colors duration-150',
				'focus-within:shadow-[0_0_0_1.5px_hsl(var(--primary)/0.45)]',
				// The lift is a shadow on its own layer that fades in: opacity
				// composites, where a transitioning box-shadow repaints every frame.
				'after:pointer-events-none after:absolute after:inset-0 after:rounded-[14px] after:opacity-0 after:shadow-[var(--elevation-card-hover)] after:transition-opacity after:duration-200',
				(revealed || expanded) && 'bg-surface-1-hover after:opacity-100',
				idle && !revealed && 'bg-surface-1/55',
			)}
		>
			{/* Stretched overlay: the row's header, for the pointer. A press
			    anywhere on it pins or unpins the row, like the chevron — which is
			    the one keyboard and assistive-tech control for it, so the overlay
			    is out of the tab order and the accessibility tree (two stops for
			    one action). */}
			<button
				type="button"
				tabIndex={-1}
				aria-hidden="true"
				data-testid="row-header"
				className="absolute inset-0 cursor-pointer rounded-[14px] focus:outline-none"
				onPointerDown={notePress}
				onClick={togglePin}
			/>

			<div
				className={cn(
					'grid items-center gap-y-1.5 py-3',
					API_ROW_INSET,
					API_ROW_GAP,
					API_ROW_COLUMNS,
					API_ROW_COLUMNS_LG,
					'lg:min-h-16 lg:py-0',
				)}
			>
				<VendorIcon
					{...vendorIconPropsFor(tile)}
					size="md"
					className={cn('lg:col-start-1 lg:row-start-1', idle && 'saturate-[.3]')}
				/>
				<div className="min-w-0 lg:col-start-2 lg:row-start-1">
					<div className="flex min-w-0 items-center gap-1.5">
						<h3 className="font-heading text-foreground-name truncate text-sm leading-[19px] font-semibold">
							{tile.title}
						</h3>
						{accountCount > 1 && (
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
					<p className="text-foreground-sub flex min-w-0 items-center gap-2 text-xs leading-[18px]">
						<span className="truncate">{identity}</span>
						{tile.updateAvailable && (
							<Tooltip
								content="The upstream spec has an update this API hasn't adopted yet — re-import it to pick it up."
								className="relative z-10 shrink-0 rounded-md"
								bubbleClassName="max-w-xs"
							>
								<Tag data-testid="tile-update-available">Update available</Tag>
							</Tooltip>
						)}
					</p>
				</div>
				<div className="col-start-2 min-w-0 lg:col-start-3 lg:row-start-1">
					<p
						className="text-foreground-sub truncate text-xs leading-[18px]"
						data-testid="tile-capability"
					>
						{capability}
					</p>
					{tile.awaitingConsent ? (
						<StatusText
							tone="muted"
							size="sm"
							plain
							icon={LogIn}
							className="text-foreground-sub max-w-full"
						>
							<span className="truncate">Sign-in at {tile.vendor} unfinished</span>
						</StatusText>
					) : (
						<p
							className="text-foreground-faint flex min-w-0 items-center gap-1.5 text-xs leading-[18px]"
							data-testid="tile-credential"
						>
							<KeyRound aria-hidden="true" className="h-3 w-3 shrink-0" />
							{credentialLabel ? (
								<>
									<span className="shrink-0">
										Credential<span className="sr-only">: </span>
									</span>
									<b
										className="text-foreground-sub min-w-0 truncate font-semibold"
										data-testid="tile-credential-label"
									>
										{credentialLabel}
									</b>
								</>
							) : (
								'No credential'
							)}
						</p>
					)}
				</div>
				{/* The third column: below lg its two cells join the row's own
				    grid (display: contents); at lg it splits into metric | status. */}
				<div
					className={cn(
						'contents lg:col-start-4 lg:row-start-1 lg:grid lg:items-center',
						API_ROW_GAP,
						API_ROW_METRIC_STATUS_LG,
					)}
				>
					<div
						data-testid="row-metric"
						className="col-start-2 flex min-w-0 items-center gap-2.5 text-xs lg:col-start-1"
					>
						{usage === undefined ? (
							<Skeleton className="h-4 w-24" />
						) : usage === null ? null : usage.total > 0 ? (
							<>
								<span className="text-foreground-sub whitespace-nowrap">
									<b className="text-foreground-lighter tabular-nums">
										{usage.total.toLocaleString()}
									</b>{' '}
									calls<span className="text-foreground-faint"> · 7d</span>
								</span>
								<SparklineChart
									data={usage.trend}
									width={44}
									height={16}
									className="text-primary opacity-85"
								/>
							</>
						) : (
							<span className="text-foreground-faint whitespace-nowrap">
								{blocked ? 'No calls · blocked' : 'No calls yet'}
							</span>
						)}
					</div>
					<div className="col-start-2 flex min-w-0 flex-col items-start gap-0.5 lg:col-start-2">
						<TileStatusText
							status={status}
							apiTitle={tile.title}
							onOpenRules={() => openSheet(onOpenRules ?? onOpen)}
							onRetry={onRetryRules}
						/>
						{(summary || tile.suspended) && (
							<div className="flex max-w-full min-w-0 items-center gap-1.5">
								{summary && (
									<p
										className="text-foreground-faint min-w-0 truncate text-[11.5px] leading-4"
										data-testid="tile-rules-summary"
									>
										{summary}
									</p>
								)}
								{tile.suspended && onResume && (
									// Above the overlay, on the summary's own line (so the row
									// keeps its height): a paused binding's one-step way back.
									<Tooltip
										content="Resume this binding — its rules are intact."
										interactiveChild
										className="relative z-10 shrink-0"
									>
										<InlineAction
											{...HOVER_INTENT_IGNORE}
											disabled={bindingPending}
											aria-busy={bindingPending || undefined}
											onClick={onResume}
											aria-label={`Resume ${tile.title} access`}
											data-testid="row-resume"
											className="text-foreground-sub hover:text-foreground focus-visible:text-foreground -mx-1.5 px-1.5 font-bold disabled:cursor-wait disabled:opacity-60"
										>
											{bindingPending ? (
												<Loader2
													aria-hidden="true"
													className="h-3 w-3 animate-spin motion-reduce:animate-none"
												/>
											) : (
												<PlayCircle
													aria-hidden="true"
													className="h-3 w-3"
												/>
											)}
											Resume
										</InlineAction>
									</Tooltip>
								)}
							</div>
						)}
						{tile.awaitingConsent && canConnect && (
							// Above the overlay: the fix for an unfinished sign-in.
							<Button
								variant="tonal"
								size="xs"
								className="relative z-10 mt-1"
								{...HOVER_INTENT_IGNORE}
								onClick={() => openSheet(onOpen)}
							>
								Finish connecting →
							</Button>
						)}
					</div>
				</div>
				{/* The row's controls, above the overlay: Manage access (the sheet),
				    then the accordion chevron (this row's pin). A hover-intent dead
				    zone: resting on them never previews the row, so reaching for
				    Manage access doesn't flash the reveal before the sheet. */}
				<div
					{...HOVER_INTENT_IGNORE}
					data-testid="row-controls"
					className="relative z-10 col-start-3 row-start-1 flex items-center gap-1 justify-self-end lg:col-start-5"
				>
					<Tooltip content="Manage access" interactiveChild>
						<Button
							ref={manageRef}
							variant="ghost"
							size="icon-xs"
							data-testid="row-manage-access"
							aria-label={manageLabel}
							aria-haspopup="dialog"
							aria-expanded={expanded}
							aria-controls={expanded ? sidebarId : undefined}
							onClick={(event) => {
								event.stopPropagation();
								manage();
							}}
						>
							<Settings2 aria-hidden="true" className="h-4 w-4" />
						</Button>
					</Tooltip>
					<Button
						variant="ghost"
						size="icon-xs"
						data-testid="row-toggle"
						data-state={revealed ? 'open' : 'closed'}
						data-pinned={pinned || undefined}
						aria-label={`${tile.title} details`}
						aria-expanded={revealed}
						aria-controls={revealed ? revealId : undefined}
						onPointerDown={notePress}
						onClick={(event) => {
							event.stopPropagation();
							togglePin();
						}}
					>
						{/* Turns with what is visibly open — a pin or a preview — on
						    the reveal's own clock: as the content unfolds, and back
						    as it folds. */}
						<ChevronRight
							aria-hidden="true"
							data-testid="row-toggle-chevron"
							className={cn(
								'h-4 w-4',
								revealed && 'rotate-90',
								revealed || expanded ? 'text-primary' : 'text-foreground-faint',
							)}
							style={{
								transition: reducedMotion
									? 'none'
									: `transform ${revealed ? REVEAL_MOTION.openMs : REVEAL_MOTION.closeMs}ms ${REVEAL_MOTION.ease}, color 150ms ease-out`,
							}}
						/>
					</Button>
				</div>
			</div>

			<ExpandReveal
				ref={revealRef}
				id={revealId}
				open={revealed}
				className={cn(
					'relative z-10',
					// What a tapped-open reveal scrolls clear of: the sticky agent
					// strip and pinned agent card above (the panel sets the var), the
					// floating dock and the bottom nav below.
					'scroll-mt-[var(--agent-pinned-clearance,5rem)] scroll-mb-[calc(9rem+env(safe-area-inset-bottom))] md:scroll-mb-[calc(5.5rem+env(safe-area-inset-bottom))]',
				)}
				onOpened={() => {
					// A row tapped open near the bottom grows behind the dock:
					// bring what it grew — down to its actions — into view.
					if (scrollOnOpen.current) {
						scrollOnOpen.current = false;
						revealRef.current?.scrollIntoView({
							block: 'nearest',
							behavior: reducedMotion ? 'auto' : 'smooth',
						});
					}
				}}
			>
				<div className={cn(API_ROW_INSET, 'pb-3.5')}>
					<ApiRowReveal
						agentId={agentId}
						tile={tile}
						status={status}
						activity={activity}
						accountCount={accountCount}
						bindingPending={bindingPending}
						onOpen={manage}
						onOpenRules={() => openSheet(onOpenRules ?? onOpen)}
						onSuspend={onSuspend}
						onResume={onResume}
					/>
				</div>
			</ExpandReveal>
		</article>
	);
});
