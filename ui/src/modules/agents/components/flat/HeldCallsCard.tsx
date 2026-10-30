/**
 * HeldCallsCard — "Waiting for you" on the selected agent: the calls an Ask
 * rule holds for the viewer's decision. Built like an API row (a
 * ShieldQuestion tile, a title over one summary line, the call names on wide
 * screens, a chevron), it sits first under the agent card and scrolls with the
 * API rows.
 *
 * It opens like an API row: a mouse at rest on its head previews the list
 * (`useHoverIntent`, never over the head's own controls), and a press anywhere
 * on the head — or on the chevron, the one keyboard control — pins it open; the
 * same again unpins it. The pin is the list's when the caller holds it
 * (`pinned` / `onPinnedChange`, from the same pin stack as the API rows, so
 * Expand all and Collapse all take the card with them). Ticking a call pins it too, so a selection never folds
 * away under a moving pointer. The list grows and folds in place
 * (`ExpandReveal`), and its ticks drop once it has folded: nothing stays
 * selected out of sight.
 *
 * "Review all" opens the review pop-up on the agent's oldest call; the pop-up
 * walks this agent's calls alone, as it does from any row here. Open, each call is
 * a row (method, headline, API, age and expiry) that opens the pop-up on
 * itself. Every row has its checkbox, and one toolbar above them, beside the
 * checkboxes, holds select-all and the bulk verbs: Deny N decides each ticked
 * call and sums up in one toast, and Approve N… opens the bulk-approve
 * confirmation on the ticked calls that are not deletes (each delete is
 * approved on its own). The pop-up and the confirmation are the shell's,
 * opened through `useReviewLinks`.
 *
 * Amber is an accent only — the tile's glyph, the count and the hairline
 * ring — so dark surfaces stay neutral; the light theme adds a faint warm fill
 * to the head and the tile. The strip tab's count chip and the agent card's
 * status beside Active live here too, so the three cues share one treatment.
 */
import { Fragment, useEffect, useId, useRef, useState } from 'react';
import { useReducedMotionConfig } from 'framer-motion';
import { ChevronRight, ShieldQuestion, Trash2 } from 'lucide-react';
import {
	Button,
	Checkbox,
	ExpandReveal,
	MethodBadge,
	REVEAL_MOTION,
	Tag,
	toast,
} from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import { HOVER_INTENT_IGNORE, useHoverIntent } from '@/shared/hooks';
import {
	ATTENTION_CHIP_CLASS,
	ATTENTION_FILL_CLASS,
	ATTENTION_RING_CLASS,
	ATTENTION_TILE_CLASS,
	ApprovalDecision,
	expiresIn,
	heldCallRoute,
	isAlreadySettled,
	isDeleteCall,
	useDecideApproval,
	useHeldCallHeadlines,
	useRefreshApprovalSurfaces,
	useReviewLinks,
	waitingSince,
	type HeldCall,
} from '@/shared/approvals';
import { useNow } from '@/shared/app/rail/useNow';
import { denySummary, type DenyOutcome } from '@/modules/agents/lib/heldCalls';

const callCount = (n: number) => `${n} ${n === 1 ? 'call' : 'calls'}`;

/** Names on the head's one line; past them, a count. The open list has every call. */
const NAMED_CALLS = 8;

interface HeldCallsCardProps {
	/** The agent's pending held calls, oldest first. Nothing renders while empty. */
	calls: readonly HeldCall[];
	/** The agent's name, for the deny summary. */
	agentName: string;
	/** The name of the agent's API a call goes to, as its API row shows it. */
	apiTitleOf: (call: HeldCall) => string;
	/** The list holds this card open (a pin); the card keeps its own when absent. */
	pinned?: boolean;
	/** Pin or unpin this card, when the list holds the pin. */
	onPinnedChange?: (pinned: boolean) => void;
}

export function HeldCallsCard({
	calls,
	agentName,
	apiTitleOf,
	pinned: pinnedProp,
	onPinnedChange,
}: HeldCallsCardProps) {
	const headlineOf = useHeldCallHeadlines(calls);
	const { openReview, openBatch } = useReviewLinks();
	// Quiet and batched: a bulk deny reports once, and refreshes once, when
	// every decision is back.
	const decide = useDecideApproval({ quiet: true, batch: true });
	const refresh = useRefreshApprovalSurfaces();
	const now = useNow();
	const reducedMotion = useReducedMotionConfig();
	// Open by a pin (a press on the head or the chevron), or previewed by a
	// mouse at rest on the head. Hover leaves a pinned card alone.
	const [ownPin, setOwnPin] = useState(false);
	const pinned = pinnedProp ?? ownPin;
	const setPinned = (next: boolean) => {
		if (pinnedProp == null) setOwnPin(next);
		onPinnedChange?.(next);
	};
	const hover = useHoverIntent<HTMLElement>({ pinned });
	const revealed = pinned || hover.open;
	const [tickedIds, setTickedIds] = useState<ReadonlySet<string>>(() => new Set());
	const [denying, setDenying] = useState(false);
	const titleId = useId();
	const listId = useId();

	// Ticks follow the list: a call that leaves it (decided elsewhere, expired)
	// drops its tick, so it is not ticked again should the list bring it back.
	// An emptied card lets go of its pin too: it renders nothing, so its fold
	// never fires, and the next call would find it open with stale ticks.
	const onPinnedChangeRef = useRef(onPinnedChange);
	useEffect(() => {
		onPinnedChangeRef.current = onPinnedChange;
	}, [onPinnedChange]);
	const wasEmpty = useRef(calls.length === 0);
	useEffect(() => {
		setTickedIds((prev) => {
			const kept = new Set([...prev].filter((id) => calls.some((call) => call.id === id)));
			return kept.size === prev.size ? prev : kept;
		});
		if (calls.length === 0 && !wasEmpty.current) {
			setOwnPin(false);
			onPinnedChangeRef.current?.(false);
		}
		wasEmpty.current = calls.length === 0;
	}, [calls]);

	if (calls.length === 0) return null;

	// Ticks on calls that have since gone (decided elsewhere, expired) count
	// for nothing.
	const ticked = calls.filter((call) => tickedIds.has(call.id));
	const approvable = ticked.filter((call) => !isDeleteCall(call));
	const deletesTicked = ticked.length - approvable.length;
	const allTicked = ticked.length === calls.length;
	const n = calls.length;
	const names = [
		...calls.slice(0, NAMED_CALLS).map(headlineOf),
		...(n > NAMED_CALLS ? [`+${n - NAMED_CALLS} more`] : []),
	].join(' · ');
	// The call whose window closes first (not always the oldest); none when no
	// call states a readable expiry.
	const soonest = calls.reduce<HeldCall | null>((best, call) => {
		const at = Date.parse(call.expires_at);
		if (Number.isNaN(at)) return best;
		return best && Date.parse(best.expires_at) <= at ? best : call;
	}, null);
	const nextExpiry = soonest ? expiresIn(soonest.expires_at, now) : '';

	/** A press on the head or the chevron: pin the card open, or unpin it. A
	 * preview under the pointer shuts with the pin and stays shut until the
	 * pointer leaves (the hook's doing). */
	const togglePin = () => {
		if (pinned) hover.close();
		setPinned(!pinned);
	};
	/** A selection is a reason to stay open: ticking pins the card. */
	const setTicks = (next: ReadonlySet<string>) => {
		setTickedIds(next);
		if (next.size > 0 && !pinned) setPinned(true);
	};
	const tick = (id: string, on: boolean) => {
		const next = new Set(tickedIds);
		if (on) next.add(id);
		else next.delete(id);
		setTicks(next);
	};

	async function denyTicked() {
		const ids = ticked.map((call) => call.id);
		setDenying(true);
		// One decision at a time, as the bulk approve runs: no burst at once.
		const outcome: DenyOutcome = { denied: 0, alreadyDecided: 0, failed: 0 };
		const stillTicked = new Set<string>();
		let firstError: string | null = null;
		for (const heldExecutionId of ids) {
			try {
				await decide.mutateAsync({
					heldExecutionId,
					body: { decision: ApprovalDecision.DENY, reason: null },
				});
				outcome.denied += 1;
			} catch (error) {
				if (isAlreadySettled(error)) {
					outcome.alreadyDecided += 1;
				} else {
					outcome.failed += 1;
					stillTicked.add(heldExecutionId);
					firstError ??= error instanceof Error ? error.message : null;
				}
			}
		}
		refresh();
		setDenying(false);
		// The calls that couldn't be denied stay ticked: another try is one press.
		setTickedIds(stillTicked);
		toast(denySummary(outcome, agentName, firstError));
	}

	return (
		<article
			aria-labelledby={titleId}
			data-testid="held-calls-card"
			data-revealed={revealed || undefined}
			data-pinned={pinned || undefined}
			ref={hover.ref}
			{...hover.handlers}
			className={cn(
				'bg-surface-1 relative overflow-hidden rounded-[14px] transition-colors duration-150',
				revealed && 'bg-surface-1-hover',
				// The hairline ring, drawn over the content so the head's fill never
				// covers it.
				'after:pointer-events-none after:absolute after:inset-0 after:rounded-[14px]',
				ATTENTION_RING_CLASS,
			)}
		>
			<div
				data-testid="held-calls-head"
				className={cn(
					ATTENTION_FILL_CLASS,
					'relative grid grid-cols-[36px_auto_minmax(0,1fr)_auto] items-center gap-x-4 py-3 pr-3 pl-3.5 lg:min-h-16 lg:py-0',
				)}
			>
				{/* Stretched overlay: the whole head, for the pointer. A press
				    anywhere on it pins or unpins the card, like the chevron — which
				    is the one keyboard and assistive-tech control for it, so the
				    overlay is out of the tab order and the accessibility tree. */}
				<button
					type="button"
					tabIndex={-1}
					aria-hidden="true"
					data-testid="held-calls-head-overlay"
					className="absolute inset-0 cursor-pointer focus:outline-none"
					onClick={togglePin}
				/>
				<span
					aria-hidden="true"
					data-testid="held-calls-tile"
					className={cn(
						ATTENTION_TILE_CLASS,
						'rounded-field grid h-9 w-9 place-items-center',
					)}
				>
					<ShieldQuestion className="h-[18px] w-[18px]" />
				</span>
				<div className="flex min-w-0 items-center gap-4">
					<span className="min-w-0">
						<span
							id={titleId}
							className="font-heading text-foreground-name block truncate text-sm leading-[19px] font-semibold"
						>
							Waiting for you
						</span>{' '}
						<span className="text-foreground-sub block truncate text-xs leading-[18px]">
							<b className="text-warning font-semibold">{callCount(n)}</b> held for
							approval
							{nextExpiry && ` · next ${nextExpiry}`}
						</span>
					</span>
					{/* Above the overlay, and a hover-intent dead zone: reaching for
					    it never previews the list on the way. */}
					<Button
						{...HOVER_INTENT_IGNORE}
						variant="tonal"
						size="xs"
						className="relative z-10 shrink-0"
						onClick={(event) => {
							event.stopPropagation();
							openReview(calls[0].id);
						}}
					>
						Review all
					</Button>
				</div>
				{/* Under the overlay, like the rest of the head's copy: a cut line
				    is read in full by opening the list, where every call is a row. */}
				<p className="text-foreground-faint hidden min-w-0 truncate text-xs lg:block">
					{names}
				</p>
				{/* The chevron: the card's pin, and its one keyboard control. Turns
				    with what is visibly open — a pin or a preview — on the reveal's
				    own clock, like an API row's. */}
				<Button
					{...HOVER_INTENT_IGNORE}
					variant="ghost"
					size="icon-xs"
					className="relative z-10"
					data-testid="held-calls-toggle"
					data-state={revealed ? 'open' : 'closed'}
					data-pinned={pinned || undefined}
					aria-label={revealed ? 'Hide the calls' : 'Show the calls'}
					aria-expanded={revealed}
					aria-controls={revealed ? listId : undefined}
					onClick={(event) => {
						event.stopPropagation();
						togglePin();
					}}
				>
					<ChevronRight
						aria-hidden="true"
						data-testid="held-calls-toggle-chevron"
						className={cn(
							'h-4 w-4',
							revealed && 'rotate-90',
							revealed ? 'text-primary' : 'text-foreground-faint',
						)}
						style={{
							transition: reducedMotion
								? 'none'
								: `transform ${revealed ? REVEAL_MOTION.openMs : REVEAL_MOTION.closeMs}ms ${REVEAL_MOTION.ease}, color 150ms ease-out`,
						}}
					/>
				</Button>
			</div>

			{/* Grows and folds in place; the ticks go once the fold has finished. */}
			<ExpandReveal
				id={listId}
				open={revealed}
				data-testid="held-calls-reveal"
				onClosed={() => setTickedIds(new Set())}
			>
				<div className="border-warning/25 border-t">
					{/* One toolbar, top-left, beside the checkboxes it acts on. */}
					<div
						data-testid="held-calls-toolbar"
						className="border-hairline-row flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1.5 border-b py-2 pr-3 pl-[62px]"
					>
						<label className="text-foreground-sub flex cursor-pointer items-center gap-2.5 text-xs font-semibold">
							<Checkbox
								size="sm"
								className="-m-1"
								checked={allTicked}
								ariaLabel="Select all"
								onChange={(on) =>
									setTicks(new Set(on ? calls.map((call) => call.id) : []))
								}
							/>
							{ticked.length > 0 ? `${ticked.length} selected` : 'Select all'}
						</label>
						{ticked.length > 0 ? (
							<>
								<Button
									variant="danger"
									size="xs"
									loading={denying}
									onClick={() => void denyTicked()}
								>
									Deny {ticked.length}
								</Button>
								<Button
									variant="primary"
									size="xs"
									disabled={approvable.length === 0 || denying}
									onClick={() => openBatch(approvable.map((call) => call.id))}
								>
									{approvable.length > 0
										? `Approve ${approvable.length}…`
										: 'Approve…'}
								</Button>
								{deletesTicked > 0 && (
									<span className="text-foreground-faint text-[11.5px]">
										Deletes are approved one at a time
									</span>
								)}
							</>
						) : (
							<span className="text-foreground-faint text-[11.5px]">
								Tick calls to deny or approve several at once
							</span>
						)}
					</div>
					<ul
						aria-label={`Calls waiting for you from ${agentName}`}
						className="divide-hairline-row divide-y pl-[52px]"
					>
						{calls.map((call) => (
							<HeldCallRow
								key={call.id}
								call={call}
								headline={headlineOf(call)}
								apiTitle={apiTitleOf(call)}
								now={now}
								ticked={tickedIds.has(call.id)}
								onTick={(on) => tick(call.id, on)}
								onOpen={() => openReview(call.id)}
							/>
						))}
					</ul>
				</div>
			</ExpandReveal>
		</article>
	);
}

/** The meta line's quiet separator. */
function Sep() {
	return <span aria-hidden="true">·</span>;
}

/** One held call: its checkbox, then the control that opens it in the pop-up. */
function HeldCallRow({
	call,
	headline,
	apiTitle,
	now,
	ticked,
	onTick,
	onOpen,
}: {
	call: HeldCall;
	headline: string;
	apiTitle: string;
	now: number;
	ticked: boolean;
	onTick: (on: boolean) => void;
	onOpen: () => void;
}) {
	// A headline from the spec says what the call does; the route says what it
	// touches, so it follows. A headline that is the route already says both.
	const named = headline !== heldCallRoute(call);
	const facts = [
		<span key="api">{apiTitle}</span>,
		named && (
			<span key="path" className="max-w-full min-w-0 truncate font-mono">
				{call.path}
			</span>
		),
		<span key="age">{waitingSince(call.created_at, now)}</span>,
		<span key="expiry">{expiresIn(call.expires_at, now)}</span>,
	].filter(Boolean);
	// The `{' '}` between flex items and blocks draw nothing (whitespace there
	// is not laid out) but keep the words apart in the control's name.
	return (
		<li
			data-testid="held-call-row"
			data-held-id={call.id}
			className="hover:bg-surface-1-hover relative flex items-center gap-2.5 px-2.5 py-2 transition-colors motion-reduce:transition-none"
		>
			<Checkbox
				size="sm"
				className="-m-1 shrink-0"
				checked={ticked}
				ariaLabel={`Select ${headline}`}
				onChange={onTick}
			/>
			<button
				type="button"
				onClick={onOpen}
				className="focus-visible:ring-ring flex min-w-0 flex-1 cursor-pointer items-center gap-2.5 rounded-md text-left focus-visible:ring-2 focus-visible:outline-none"
			>
				<span className="sr-only">Review </span>
				<MethodBadge method={call.method} className="w-[52px]" />{' '}
				<span className="min-w-0 flex-1">
					<span className="text-foreground-name block truncate text-[13px] leading-5 font-semibold">
						{headline}
					</span>{' '}
					<span className="text-foreground-faint flex min-w-0 flex-wrap items-center gap-x-1 text-[11.5px] leading-4">
						{facts.map((fact, i) => (
							<Fragment key={i}>
								{i > 0 && (
									<>
										{' '}
										<Sep />{' '}
									</>
								)}
								{fact}
							</Fragment>
						))}
					</span>
				</span>
				{isDeleteCall(call) && (
					<>
						{' '}
						<Tag>
							<Trash2 aria-hidden="true" className="text-danger h-3 w-3 shrink-0" />
							Deletes data
						</Tag>
					</>
				)}
				<ChevronRight
					aria-hidden="true"
					className="text-foreground-faint h-4 w-4 shrink-0"
				/>
			</button>
		</li>
	);
}

/**
 * The strip tab's cue that the agent has calls waiting for the viewer: a
 * ShieldQuestion and the count, in the same chip as the rail's Inbox tab
 * (amber glyph on a neutral chip; light adds a faint warm fill).
 */
export function HeldCallsChip({ count }: { count: number }) {
	return (
		<span
			data-testid="strip-waiting-chip"
			className={cn(
				ATTENTION_CHIP_CLASS,
				'inline-flex h-[18px] shrink-0 items-center gap-[3px] rounded-full pr-1.5 pl-1 text-[11px] font-bold tabular-nums',
			)}
		>
			<ShieldQuestion aria-hidden="true" className="text-warning size-3 shrink-0" />
			{/* The bare digit is visual; the tab's name says what it counts. */}
			<span aria-hidden="true">{count}</span>
			<span className="sr-only">, {callCount(count)} waiting for you</span>
		</span>
	);
}

/** Beside the agent's status: it has calls waiting for the viewer. */
export function HeldCallsStatus() {
	return (
		<span
			data-testid="agent-waiting-status"
			className="text-warning inline-flex shrink-0 items-center gap-1.5 text-xs font-semibold"
		>
			<ShieldQuestion aria-hidden="true" className="size-3.5 shrink-0" />
			{/* The glyph alone on a phone, where the name needs the room. */}
			<span className="max-sm:sr-only">Waiting for you</span>
		</span>
	);
}
