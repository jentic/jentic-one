/**
 * AgentCard — the selected agent's card under the strip's notch: avatar, name,
 * a neutral status chip, one facts line (registered · owner or self-registered ·
 * id tail · last activity), one contextual action, the state banner when the
 * agent is not serving, and the KPI row.
 *
 * Status is never a hue: the chip is the neutral tonal chip with the state's
 * glyph (`STATUS_ICON`) and word (`STATUS_LABELS`), the same vocabulary as
 * every other actor status.
 */
import {
	useCallback,
	useEffect,
	useId,
	useRef,
	useState,
	type CSSProperties,
	type ReactNode,
	type TransitionEvent,
} from 'react';
import { motion, useReducedMotionConfig } from 'framer-motion';
import { ChevronsUpDown } from 'lucide-react';
import { ActorLabel, AgentBadge, CopyButton, TruncateWithTooltip } from '@/shared/ui';
import { useMediaQuery } from '@/shared/hooks';
import { shellScrollRoot } from '@/shared/lib/shellScroll';
import { commandChordLabel, commandChordShortcut } from '@/shared/lib/keyboard';
import { cn, formatTimestamp } from '@/shared/lib/utils';
import { ago } from '@/modules/agents/lib/ago';
import { useAgentPermissions, type AgentEntity } from '@/modules/agents/api';
import {
	contentTransition,
	FOLD_MOTION,
	foldTransition,
	STICK_AFTER_PX,
	STICK_BAND_PX,
	useDismissPeek,
	useMeasuredHeight,
	useStuck,
} from './useStickyFold';
import { AgentStatusMark, FactSep } from './agentCardParts';

/**
 * Folded, the stats become the grabber lip: the card's own surface running on
 * below an inset divider, with a sheet-handle grabber that bends into a
 * chevron. No figure shows folded. This is the lip's height (and the open
 * panel's footer, holding the grabber, while pinned).
 */
const LIP_H = 20;
/** The page background held under the pinned card, so the rows scrolling
 * under it never touch the strip. */
const CLEARANCE_PX = 8;
/** The grabber: a flat bar, bent into a chevron (down to open, up to close). */
type GrabberBend = 'flat' | 'down' | 'up';
const GRABBER_PATH: Record<GrabberBend, string> = {
	flat: 'M3 4 L16 4 L29 4',
	down: 'M5 2 L16 6.5 L27 2',
	up: 'M5 6 L16 1.5 L27 6',
};
/** The grabber's bend and tint. */
const GRABBER_EASE = 'cubic-bezier(0.4, 0, 0.2, 1)';

/** The most of a phone viewport the pinned card may take before it stays put. */
const PHONE_PIN_BUDGET = 0.35;

interface AgentCardProps {
	agent: AgentEntity;
	/** The newest call's time: `{ at: null }` = none yet; `null` = not visible to
	 * this viewer (the fact is left out); `undefined` = still loading. */
	lastActivity: { at: string | null } | null | undefined;
	/** The one contextual action (the tonal "Add API"), if any. */
	action?: ReactNode;
	/**
	 * The "Can call" list ⇄ cards lens toggle, at the header's right end after
	 * the action — in the card, so the tree below still starts 3px under it.
	 */
	viewToggle?: ReactNode;
	/** The agent's description, under the header. */
	description?: ReactNode;
	/** The non-serving state's banner (pending decision, rejected, archived). */
	banner?: ReactNode;
	/** The KPI row. */
	kpis?: ReactNode;
	/**
	 * Where the card pins, in px under the shell scroller's top: the agent
	 * strip's measured height, so the card docks under the strip's notch. The
	 * pin line is judged against it.
	 */
	stickyTop?: number;
	/**
	 * Where the card sits once pinned, if the strip condenses then (its header
	 * row slides away): the strip's condensed height. Defaults to `stickyTop`.
	 * The pin line stays at `stickyTop`, so condensing never feeds back into it.
	 */
	stuckTop?: number;
	/** Reports the pinned card's height, for what scrolls clear of it. */
	onPinnedHeight?: (px: number) => void;
	/** Reports whether the card is pinned (so the strip can condense over it). */
	onStuckChange?: (stuck: boolean) => void;
	/** Opens the agent picker: the name becomes its button. */
	onSwitchAgent?: () => void;
	/** Fade the content in on mount (a switch's new card), inside a frame that
	 * is there from the first frame. */
	fadeIn?: boolean;
}

/** The card content's entrance on a switch: the frame stays, the content fades. */
const CONTENT_IN = { duration: 0.18, ease: 'easeOut' } as const;

interface FoldProps {
	folded: boolean;
	/** The height it folds down to (the lip), or 0. */
	rest?: number;
	/** The content's natural height, measured off `contentRef`. */
	natural: number | undefined;
	contentRef?: (el: HTMLElement | null) => void;
	reduced: boolean;
	children: ReactNode;
	className?: string;
	/** Layered over the folded rest (the lip's own face). */
	overlay?: ReactNode;
	/** Merged over the fold's own style (a caller's own transition wins). */
	style?: CSSProperties;
	/** On the content wrapper: its surface, when the content itself folds. */
	contentClassName?: string;
	/** Merged over the content wrapper's style (e.g. the stats' clip-path). */
	contentStyle?: CSSProperties;
	/** Fade the content out as it folds (off: the caller fades its own parts). */
	fadeContent?: boolean;
	/** The caller clips (a clip-path), so the fold never sets `overflow: hidden`. */
	unclipped?: boolean;
	id?: string;
	'data-testid'?: string;
}

/**
 * A block that folds to `rest` px and back. Its height is the content's
 * measured height (never `auto` mid-move), so the spacer after the card can
 * hold exactly the room it gives up, frame for frame. It clips only while
 * folded or moving, so a popover inside it (the id reveal) isn't cut off when
 * it is open. Folded, the content is hidden from assistive tech and out of the
 * tab order.
 */
function Fold({
	folded,
	rest = 0,
	natural,
	contentRef,
	reduced,
	children,
	className,
	overlay,
	style: extra,
	contentClassName,
	contentStyle,
	fadeContent = true,
	unclipped = false,
	id,
	'data-testid': testId,
}: FoldProps) {
	// Clip while folded or moving; open and settled, nothing clips.
	const [settledAs, setSettledAs] = useState(folded);
	const clip = !unclipped && (folded || (!reduced && settledAs !== folded));
	const style: CSSProperties = {
		height: folded ? rest : natural,
		transition: foldTransition('height', folded, reduced),
		overflow: clip ? 'hidden' : undefined,
		...extra,
	};
	function onSettle(event: TransitionEvent<HTMLDivElement>) {
		if (event.target === event.currentTarget && event.propertyName === 'height') {
			setSettledAs(folded);
		}
	}
	const fade: CSSProperties = fadeContent
		? {
				opacity: folded ? 0 : 1,
				// Out from the start; in a beat after the height starts.
				transition: contentTransition(['opacity'], folded, reduced),
			}
		: {};
	return (
		<div
			id={id}
			data-testid={testId}
			data-folded={folded}
			style={style}
			className={cn('relative', className)}
			onTransitionEnd={onSettle}
			onTransitionCancel={onSettle}
		>
			<div
				ref={contentRef}
				aria-hidden={folded || undefined}
				inert={folded || undefined}
				className={contentClassName}
				style={{
					...fade,
					...contentStyle,
					transition: mergeTransitions(fade.transition, contentStyle?.transition),
				}}
			>
				{children}
			</div>
			{overlay}
		</div>
	);
}

/** One `transition` from two (either may be absent or `none`). */
function mergeTransitions(a?: string, b?: string): string | undefined {
	const parts = [a, b].filter((t): t is string => Boolean(t) && t !== 'none');
	if (parts.length > 0) return parts.join(', ');
	return a ?? b;
}

export function AgentCard({
	agent,
	lastActivity,
	action,
	viewToggle,
	description,
	banner,
	kpis,
	stickyTop = 0,
	stuckTop = stickyTop,
	onPinnedHeight,
	onStuckChange,
	onSwitchAgent,
	fadeIn = false,
}: AgentCardProps) {
	const selfRegistered = agent.attribution.registeredBy === 'self';
	const reduced = useReducedMotionConfig() ?? false;
	const compact = useMediaQuery('(max-width: 639px)');

	// Pinned once the card has scrolled a little past its resting place.
	const sentinelRef = useRef<HTMLDivElement>(null);
	// On a phone the pinned card (strip, header, banner, lip — the facts fold)
	// must stay within PHONE_PIN_BUDGET of the viewport, or it doesn't pin.
	const [headerRef, headerH] = useMeasuredHeight();
	const [bannerRef, bannerH] = useMeasuredHeight();
	const [factsRef, factsH] = useMeasuredHeight();
	const [pinnable, setPinnable] = useState(true);
	// The strip counted as it is while the card is pinned (condensed, if it
	// condenses): measured, never assumed.
	const phonePinned =
		stuckTop + (headerH ?? 0) - (factsH ?? 0) + (bannerH ?? 0) + LIP_H + 2 + CLEARANCE_PX;
	useEffect(() => {
		// Judged off the unfolded header, so folding never feeds back into it.
		if (!compact) return setPinnable(true);
		if (headerH === undefined || factsH === undefined || factsH === 0) return;
		// Counted from the viewport's top: the shell scroller starts under the navbar.
		const shellTop = shellScrollRoot()?.getBoundingClientRect().top ?? 0;
		setPinnable(shellTop + phonePinned <= window.innerHeight * PHONE_PIN_BUDGET);
	}, [compact, headerH, factsH, phonePinned]);
	const stuck = useStuck(sentinelRef, stickyTop, pinnable);
	// The stats, unfolded over the content while pinned.
	const [peekWanted, setPeek] = useState(false);
	const peeking = stuck && peekWanted;
	const closePeek = useCallback(() => setPeek(false), []);
	// Unpinning ends a peek, so the next pin starts folded.
	const [wasStuck, setWasStuck] = useState(stuck);
	if (wasStuck !== stuck) {
		setWasStuck(stuck);
		if (!stuck) setPeek(false);
	}
	useDismissPeek(peeking, closePeek);
	// Told on every change, and `false` on the way out (an agent switch
	// remounts the card).
	useEffect(() => {
		onStuckChange?.(stuck);
	}, [onStuckChange, stuck]);
	useEffect(() => () => onStuckChange?.(false), [onStuckChange]);

	const [descriptionRef, descriptionH] = useMeasuredHeight();
	const [statsRef, statsH] = useMeasuredHeight();

	const kpisGiven = kpis != null && kpis !== false;
	// The KPI row renders nothing when no figure is provable: no fold, no lip.
	const hasStats = kpisGiven && statsH !== 0;
	// Pinned, the card keeps only what identifies it and what acts on it: the
	// description folds away (a state banner stays), the stats fold to a lip,
	// and on a phone the facts line goes too.
	const factsFolded = stuck && compact;
	const descriptionFolded = stuck && Boolean(description);
	const statsFolded = stuck && hasStats && !peeking;
	// Pinned, the panel ends in a footer the strip's height, holding the
	// control (so it never hangs over the content); folded, it is the strip.
	const footer = stuck && hasStats ? LIP_H : 0;
	// The open panel's height (the figures and its bottom border), in whole
	// pixels, so its bottom edge lands on a device pixel rather than smearing
	// across two.
	const panelH = statsH === undefined ? undefined : Math.ceil(statsH) + 1;
	// The room the folds give up, held after the card so nothing below moves:
	// the card shrinks over the content instead of pulling the content up. The
	// open panel's footer comes back out of the card's bottom margin (12px, the
	// panel's `space-y-3`), so the sum stays put all through the unfold.
	const held =
		(factsFolded ? (factsH ?? 0) : 0) +
		(descriptionFolded ? (descriptionH ?? 0) : 0) +
		(statsFolded ? Math.max(0, (panelH ?? 0) - LIP_H) : 0);
	const cardGap = 12 - (stuck && hasStats && !statsFolded ? footer : 0);
	// The head closes its own bottom corners unless the stats continue it, open
	// or folded into the lip.
	const headClosed = !kpisGiven || statsH === 0;
	const statsId = useId();
	// Every block folds in step, on FOLD_MOTION's one curve: tucking in (the
	// card pinned, the stats not peeking) or coming back out.
	const tucking = stuck && !peeking;
	const fade = reduced ? 'none' : `opacity ${FOLD_MOTION.foldMs}ms ${FOLD_MOTION.ease}`;
	// A switch mounts a new card: its content fades in inside a frame that is
	// there from the first frame, so the strip's notch always lands on a solid
	// top edge.
	const contentEntrance = fadeIn && !reduced ? { opacity: 0 } : false;
	const grow = (prop: string) => foldTransition(prop, tucking, reduced);
	// How far the pinned card rides up under a condensed strip (≤ 0).
	const lift = stuck ? Math.min(0, stuckTop - stickyTop) : 0;
	// The panel, folded: clipped to the lip's rect, a pixel inside its sides so
	// the lip's own border is the only one there; it grows out to the whole
	// panel.
	const panelClip: CSSProperties = {
		clipPath: statsFolded
			? `inset(0 1px calc(100% - ${LIP_H}px) 1px round 0 0 var(--radius-panel) var(--radius-panel))`
			: 'inset(0 0 0 0 round 0 0 var(--radius-panel) var(--radius-panel))',
		// The pinned panel's footer, where the control sits.
		paddingBottom: footer,
		// Whole pixels, filling the fold exactly (see `panelH`).
		minHeight: panelH === undefined ? undefined : panelH + footer,
		transition: grow('clip-path'),
	};
	// The lip's own face, over the panel: folding, it is there from the start,
	// so the panel collapses beneath it; unfolding, it goes as the panel grows.
	const stripFace: CSSProperties = {
		opacity: statsFolded ? 1 : 0,
		pointerEvents: statsFolded ? undefined : 'none',
		// Either way, straight away: folding, it is over the panel from the
		// start; unfolding, it goes as the panel grows.
		transition: contentTransition(['opacity'], true, reduced),
	};
	// The figures fade and drift up with the height (from t=0), and come back
	// once the panel is under way.
	const figures: CSSProperties = {
		opacity: statsFolded ? 0 : 1,
		transform: statsFolded ? `translateY(-${FOLD_MOTION.contentLiftPx}px)` : undefined,
		transition: contentTransition(['opacity', 'transform'], statsFolded, reduced),
	};
	// The header's shadow onto the panel tucking under it: in with the fold.
	const tuckShadow: CSSProperties = {
		opacity: statsFolded ? 1 : 0,
		transition: grow('opacity'),
	};
	// The grabber bends into a chevron when hovered or focused (down), and
	// points up while the stats are open.
	const [grabHint, setGrabHint] = useState(false);
	const grabBend: GrabberBend = peeking ? 'up' : grabHint ? 'down' : 'flat';

	// The pinned stack's height (header, banner, strip, borders and the
	// clearance under it): what a row's reveal scrolls clear of. On a phone the
	// facts fold too; counting them errs on the side of more room.
	const pinnedHeight =
		(headerH ?? 0) + (bannerH ?? 0) + (hasStats ? LIP_H : 0) + 2 + CLEARANCE_PX;
	useEffect(() => {
		onPinnedHeight?.(pinnable ? pinnedHeight : 0);
	}, [onPinnedHeight, pinnedHeight, pinnable]);

	return (
		// `contents`: the sentinel, the card and its spacer sit straight in the
		// panel, so the card's sticky range is the whole "Can call" list.
		<div className="contents">
			<div
				ref={sentinelRef}
				aria-hidden="true"
				data-testid="agent-card-sentinel"
				className="pointer-events-none relative"
				style={{ top: STICK_AFTER_PX, height: STICK_BAND_PX, marginBottom: -STICK_BAND_PX }}
			/>
			<div
				data-testid="agent-card"
				data-status={agent.status}
				data-stuck={stuck}
				data-stats={hasStats ? (statsFolded ? 'folded' : 'open') : undefined}
				style={{
					top: pinnable ? stickyTop : undefined,
					// Pinned under a condensed strip, the card rides up with it: the
					// same transform on the same curve, so the notch never parts
					// from the card's top edge. The pin line stays at `stickyTop`.
					transform: lift ? `translateY(${lift}px)` : undefined,
					// Stands in for the panel's `space-y-3`, which a `contents`
					// wrapper doesn't pass on; less the open panel's footer.
					marginBottom: cardGap,
					transition: mergeTransitions(
						grow('margin-bottom'),
						foldTransition('transform', stuck, reduced),
					),
				}}
				className={cn('z-[15]', pinnable ? 'sticky' : 'relative')}
			>
				{/* Pinned: the page background held behind the whole stack and the
				    band under it — so nothing scrolling under shows past the card's
				    rounded corners, and the rows pass under the band cleanly. It is
				    the page's own colour, so it switches with the pin rather than
				    fading (a half-faded band would let a row show square past the
				    corners). Then a soft shadow onto the rows, the card's own
				    silhouette (same box, same radius) blurred, over the band and
				    under the card's surfaces — faded by opacity alone (the
				    box-shadow never animates). */}
				<span
					aria-hidden="true"
					data-testid="agent-card-clearance"
					className="bg-background pointer-events-none absolute inset-x-0 top-0"
					style={{ bottom: -CLEARANCE_PX, opacity: stuck ? 1 : 0 }}
				/>
				<span
					aria-hidden="true"
					data-testid="agent-card-drop-shadow"
					className="rounded-panel pointer-events-none absolute inset-0 shadow-[0_10px_24px_-6px_rgb(0_0_0/0.45)]"
					style={{ opacity: stuck ? 1 : 0, transition: fade }}
				/>
				{/* The card's upper part — its own surface, which the stats continue:
				    with stats it has no bottom edge (a pixel of its own surface
				    instead, so its sides run straight down with no corner mitre
				    into the panel's sides); alone, it closes its own corners. */}
				<div
					data-testid="agent-card-head"
					className={cn(
						'bg-surface-1 border-hairline-field rounded-t-panel relative',
						headClosed ? 'rounded-b-panel border' : 'border-x border-t pb-px',
					)}
				>
					<motion.div
						data-testid="agent-card-content"
						initial={contentEntrance}
						animate={{ opacity: 1 }}
						transition={CONTENT_IN}
					>
						{/* Below sm the CTA drops to its own full-width line, so the name and
			    its facts keep the card's width. */}
						<header
							ref={headerRef}
							className="flex flex-wrap items-center gap-x-4 gap-y-3.5 px-4 pt-4 pb-4 sm:px-5 sm:pt-5 sm:pb-[18px]"
						>
							<AgentBadge id={agent.id} name={agent.name} size="lg" shape="circle" />
							<div className="min-w-0 flex-1">
								<div className="flex min-w-0 items-center gap-2.5">
									{/* One line at the card's full width; the full name in a tooltip
						    only when it is actually cut. With a picker to open, the name
						    is its button — inside the heading, so the heading stays. */}
									<h2 className="font-heading text-foreground-name min-w-0 text-[22px] leading-7 font-semibold tracking-[-0.015em]">
										{onSwitchAgent ? (
											<AgentSwitcher
												name={agent.name}
												onOpen={onSwitchAgent}
											/>
										) : (
											<TruncateWithTooltip>{agent.name}</TruncateWithTooltip>
										)}
									</h2>
									{/* The tab's own treatment: the lifecycle glyph in its status tint,
						    the word beside it — no tinted pill. */}
									<AgentStatusMark
										status={agent.status}
										data-testid="agent-status-chip"
									/>
								</div>
								{/* On a phone the facts fold away while the card is pinned. */}
								<Fold
									folded={factsFolded}
									natural={factsH}
									contentRef={factsRef}
									reduced={reduced}
								>
									<p
										data-testid="agent-facts-line"
										className="text-foreground-sub flex min-w-0 flex-wrap items-center pt-1 text-[12.5px] leading-5"
									>
										{/* The full timestamp is already the visible text — no hover hint. */}
										<span>Registered {formatTimestamp(agent.createdAt)}</span>
										{selfRegistered ? (
											<>
												<FactSep />
												<span>Self-registered</span>
											</>
										) : agent.ownerId ? (
											<>
												<FactSep />
												<span>
													Owner <ActorLabel actorId={agent.ownerId} />
												</span>
											</>
										) : null}
										{/* The key id is a desktop nicety; the access sheet has it on mobile. */}
										<span className="hidden sm:contents">
											<FactSep />
											<AgentIdReveal agent={agent} />
										</span>
										{lastActivity !== null && (
											<>
												<FactSep />
												<span data-testid="agent-last-activity">
													Last activity{' '}
													{lastActivity === undefined
														? '…'
														: lastActivity.at
															? ago(lastActivity.at)
															: '—'}
												</span>
											</>
										)}
									</p>
								</Fold>
							</div>
							{(action || viewToggle) && (
								// Below sm the CTA takes the full width and the toolbar (Expand
								// all, the lens toggle) wraps under it, pinned right; from sm
								// they share one line after the facts.
								<div className="flex basis-full flex-wrap items-center justify-end gap-3 sm:shrink-0 sm:basis-auto sm:flex-nowrap">
									{action && (
										<div className="min-w-0 flex-1 max-sm:basis-full sm:flex-none [&>*]:w-full sm:[&>*]:w-auto">
											{action}
										</div>
									)}
									{viewToggle && (
										<div
											data-testid="can-call-toolbar"
											className="flex shrink-0 items-center"
										>
											{viewToggle}
										</div>
									)}
								</div>
							)}
						</header>
						{description && (
							<Fold
								folded={descriptionFolded}
								natural={descriptionH}
								contentRef={descriptionRef}
								reduced={reduced}
								className="sm:-mt-2"
							>
								<div className="px-4 pb-4 text-left sm:px-5 sm:pl-[84px]">
									{description}
								</div>
							</Fold>
						)}
						{banner && (
							<div ref={bannerRef} className="px-4 pb-4 sm:px-5 sm:pb-5">
								{banner}
							</div>
						)}
					</motion.div>
				</div>
				{kpisGiven && (
					<Fold
						id={statsId}
						data-testid="agent-stats-fold"
						folded={statsFolded}
						rest={LIP_H}
						natural={panelH === undefined ? undefined : panelH + footer}
						reduced={reduced}
						unclipped
						// The surface stays opaque while it grows; only the figures fade.
						fadeContent={false}
						style={{ transition: grow('height') }}
						// The stats' own surface, closing the card's sides and bottom.
						contentClassName="bg-surface-1 border-hairline-field rounded-b-panel relative border-x border-b"
						contentStyle={panelClip}
						overlay={
							// The lip: the card's own surface running on, under an
							// inset divider.
							<span
								aria-hidden="true"
								data-testid="agent-stats-strip"
								// The whole strip takes a click while folded (the panel
								// under it is inert); the button is the keyboard's (and
								// the a11y tree's) control.
								onClick={statsFolded ? () => setPeek(true) : undefined}
								onPointerEnter={() => setGrabHint(true)}
								onPointerLeave={() => setGrabHint(false)}
								className="bg-surface-1 border-hairline-field rounded-b-panel absolute inset-x-0 top-0 z-[1] cursor-pointer border-x border-b"
								style={{ ...stripFace, height: LIP_H }}
							>
								<span className="bg-hairline-field absolute inset-x-4 top-0 h-px" />
							</span>
						}
					>
						{/* No figure shows folded: they fade in as the panel opens. */}
						<div ref={statsRef} data-testid="agent-stats-figures" style={figures}>
							<motion.div
								initial={contentEntrance}
								animate={{ opacity: 1 }}
								transition={CONTENT_IN}
							>
								{kpis}
							</motion.div>
						</div>
						{/* The header's shadow onto the panel as it tucks under the lip
						    (clipped away with it once folded). */}
						<span
							aria-hidden="true"
							data-testid="agent-stats-tuck-shadow"
							className="pointer-events-none absolute inset-x-px h-3 bg-linear-to-b from-black/25 to-transparent"
							style={{ ...tuckShadow, top: LIP_H }}
						/>
					</Fold>
				)}
				{hasStats && (
					// The control: centred inside the strip, or the open panel's
					// footer. Out of reach (and the tree) until the card pins.
					<div
						inert={!stuck || undefined}
						aria-hidden={!stuck || undefined}
						className="absolute bottom-0 left-1/2 z-10 flex items-center justify-center"
						style={{
							height: LIP_H,
							opacity: stuck ? 1 : 0,
							pointerEvents: stuck ? undefined : 'none',
							transform: 'translateX(-50%)',
							transition: fade,
						}}
					>
						{/* A sheet handle: a bar that bends into a chevron. */}
						<button
							type="button"
							aria-label="Show agent stats"
							aria-expanded={peeking}
							aria-controls={statsId}
							data-testid="agent-stats-toggle"
							data-bend={grabBend}
							onClick={() => setPeek((open) => !open)}
							onPointerEnter={() => setGrabHint(true)}
							onPointerLeave={() => setGrabHint(false)}
							onFocus={(e) => setGrabHint(e.currentTarget.matches(':focus-visible'))}
							onBlur={() => setGrabHint(false)}
							className="focus-visible:ring-ring focus-visible:ring-offset-surface-1 relative flex h-4 w-12 items-center justify-center rounded-full after:absolute after:-inset-1 after:content-[''] focus-visible:ring-2 focus-visible:ring-offset-1 focus-visible:outline-none"
						>
							<svg
								aria-hidden="true"
								viewBox="0 0 32 8"
								className={cn(
									'h-2 w-8',
									grabHint || peeking
										? 'text-muted-foreground/80'
										: 'text-muted-foreground/40',
								)}
								style={{
									transition: reduced ? 'none' : `color 200ms ${GRABBER_EASE}`,
								}}
							>
								{/* One stroke, so the bend never doubles up at the joint. */}
								<path
									d={GRABBER_PATH[grabBend]}
									fill="none"
									stroke="currentColor"
									strokeWidth={3}
									strokeLinecap="round"
									strokeLinejoin="round"
									style={
										{
											d: `path("${GRABBER_PATH[grabBend]}")`,
											transition: reduced
												? 'none'
												: `d 200ms ${GRABBER_EASE}`,
										} as CSSProperties
									}
								/>
							</svg>
						</button>
					</div>
				)}
			</div>
			{/* Holds the room the folds give up, so the content never moves. */}
			<div
				aria-hidden="true"
				data-testid="agent-card-spacer"
				style={{ height: held, transition: grow('height') }}
			/>
		</div>
	);
}

/**
 * The card's name as the agent picker's button: the name (cut with a tooltip
 * when long) and a muted up-down chevron that brightens on hover, on a ghost
 * tint whose padding is taken back out of the margin, so the hit area never
 * moves the name. Clicking it focuses it first (Safari doesn't), so the picker
 * can hand focus back to it.
 */
function AgentSwitcher({ name, onOpen }: { name: string; onOpen: () => void }) {
	return (
		<button
			type="button"
			data-agent-switcher=""
			data-testid="agent-card-switcher"
			aria-label={`${name} — switch agent (${commandChordLabel('k')})`}
			aria-haspopup="dialog"
			aria-keyshortcuts={commandChordShortcut('k')}
			onClick={(e) => {
				e.currentTarget.focus({ preventScroll: true });
				onOpen();
			}}
			className={cn(
				'group/switch -mx-1.5 -my-0.5 flex max-w-[calc(100%+0.75rem)] min-w-0 items-center gap-1 rounded-lg px-1.5 py-0.5 text-left transition-colors duration-150',
				'hover:bg-tint-2 focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none',
			)}
		>
			{/* Inside a control whose name carries the full text: hover-only. */}
			<TruncateWithTooltip focusable={false} className="min-w-0">
				{name}
			</TruncateWithTooltip>
			<ChevronsUpDown
				aria-hidden="true"
				className="text-muted-foreground/60 group-hover/switch:text-foreground-sub group-focus-visible/switch:text-foreground-sub size-4 shrink-0 transition-colors duration-150"
			/>
		</button>
	);
}

/**
 * The id's tail, revealing the full id, a copy button and the permission count
 * on hover or keyboard focus — after the same ~130ms intent as the rows, so a
 * pointer passing over the facts line doesn't flash it.
 */
function AgentIdReveal({ agent }: { agent: AgentEntity }) {
	const permissions = useAgentPermissions(agent.id);
	const count = permissions.data?.length;
	const verb = agent.status === 'pending' ? 'requested' : 'granted';
	return (
		<span
			// Focusable so a keyboard reaches the copy button inside the reveal.
			tabIndex={0}
			data-testid="agent-id-reveal"
			className="group/id focus-visible:ring-ring relative rounded-sm font-mono text-[11.5px] focus-visible:ring-2 focus-visible:outline-none"
		>
			<span aria-hidden="true">…{agent.id.slice(-8)}</span>
			<span className="sr-only">Agent ID {agent.id}</span>
			<span
				role="group"
				aria-label="Agent ID"
				className={cn(
					'bg-surface-sheet shadow-pop border-hairline-field absolute top-[calc(100%+6px)] left-1/2 z-40 flex -translate-x-1/2 items-center gap-2 rounded-lg border py-1.5 pr-1.5 pl-2.5 whitespace-nowrap',
					'pointer-events-none invisible translate-y-0.5 opacity-0 transition-[opacity,transform,visibility] duration-150',
					'group-focus-within/id:pointer-events-auto group-focus-within/id:visible group-focus-within/id:translate-y-0 group-focus-within/id:opacity-100',
					'group-hover/id:pointer-events-auto group-hover/id:visible group-hover/id:translate-y-0 group-hover/id:opacity-100 group-hover/id:delay-[130ms]',
					'motion-reduce:transition-none',
				)}
			>
				<code className="text-foreground-lighter text-[11.5px]">{agent.id}</code>
				<CopyButton
					value={agent.id}
					size="icon"
					variant="ghost"
					ariaLabel="Copy agent id"
				/>
				{count != null && (
					<>
						<span aria-hidden="true" className="text-foreground-faint font-sans">
							·
						</span>
						<span className="text-foreground-sub font-sans text-xs">
							{count} {count === 1 ? 'permission' : 'permissions'} {verb}
						</span>
					</>
				)}
			</span>
		</span>
	);
}
