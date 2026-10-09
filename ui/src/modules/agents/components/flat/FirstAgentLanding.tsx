/**
 * FirstAgentLanding — the Agents page with no agents in the org yet, and the
 * place its first self-registered agent is finished.
 *
 * Two routes in, side by side: the primary card has the agent register itself
 * (`jentic register`); the secondary card is manual creation, in the New
 * agent panel's "Create here" tab. Below them, a faint tonal preview of the
 * fleet view with an empty slot for the first agent.
 *
 * The parent polls (and the event stream invalidates) the agents list while
 * this is on screen and passes the registered agent in as `agent`. The card
 * follows that agent's status, not a click here, so an approval from the
 * Activity rail or the bell lands the same way:
 *  - pending: the manual card slides off under the Activity rail and the
 *    primary card grows into the row; the command gives way to the agent's
 *    details with Approve / Deny, and the stepper marks steps 1–2 done.
 *  - active: steps 1–3 are done and "Give it an API" is current, as the
 *    actions give way to the first-API suggestion (GitHub when the workspace
 *    or catalog has it).
 * Each choice there goes to `onExit`, and the parent swaps in the fleet view.
 * A denied agent drops out of `agent`, and the card is back to listening.
 * Reduced motion swaps each state at once.
 *
 * On a reload the parent may mount this straight into `arrived` or `approved`
 * (it resumes from the org's data). That state is simply rendered — no manual
 * card sliding off, no tab pop, no focus move — and only later, in-session
 * changes animate and move focus to the card's new heading. The card's content
 * is `RegisterFlow`, which the New agent panel renders too.
 */
import { useId, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from 'react';
import {
	AnimatePresence,
	motion,
	useIsPresent,
	useReducedMotionConfig,
	type Transition,
} from 'framer-motion';
import { KeyRound, Pencil, Plus, UserRound } from 'lucide-react';
import { Button, McpIcon } from '@/shared/ui';
import { useMediaQuery } from '@/shared/hooks';
import type { AgentEntity } from '@/modules/agents/api';
import { EASE_OUT_SOFT, GhostFleet } from '@/modules/agents/components/flat/GhostFleet';
import { RegisterFlow, registerFlowTitleId } from '@/modules/agents/components/flat/RegisterFlow';
import type { FirstAgentExit, FirstAgentPhase } from '@/modules/agents/lib/firstRun';

interface FirstAgentLandingProps {
	/** Opens the New agent panel — the same handler as the page header's button. */
	onCreateAgent: () => void;
	/** The self-registered agent being finished here: pending or active. */
	agent: AgentEntity | null;
	onApprove: () => void;
	approvePending: boolean;
	/** Opens the page's deny dialog for `agent`. */
	onDeny: () => void;
	/** Show the fleet with `agent` selected, and the Add-APIs flow if asked. */
	onExit: (exit: FirstAgentExit) => void;
	/** The name typed in the register card. */
	registerName: string;
	onRegisterNameChange: (name: string) => void;
	/** The name the displayed command registers with. */
	commandName: string;
	/** The existing agent name the typed one duplicates, or `null`. */
	registerNameDuplicateOf: string | null;
	/** The name an arrival should carry (the command's), or null when this
	 * session never showed the command. */
	expectedName: string | null;
	/** Other agents waiting for approval besides `agent`. */
	morePending: number;
	/** Leave for the fleet view, where every pending agent is listed. */
	onShowFleet: () => void;
	/** The preview's agent slot, where the hand-off to the strip starts. */
	slotRef: RefObject<HTMLElement | null>;
}

export function FirstAgentLanding({
	onCreateAgent,
	agent,
	onApprove,
	approvePending,
	onDeny,
	onExit,
	registerName,
	onRegisterNameChange,
	commandName,
	registerNameDuplicateOf,
	expectedName,
	morePending,
	onShowFleet,
	slotRef,
}: FirstAgentLandingProps) {
	const reducedMotion = useReducedMotionConfig() ?? false;
	const agentPhase = agent?.status === 'pending' ? 'arrived' : 'approved';
	const phase: FirstAgentPhase = agent == null ? 'listening' : agentPhase;
	const morph: Transition = reducedMotion
		? { duration: 0 }
		: { duration: 0.56, ease: EASE_OUT_SOFT };
	const fade: Transition = reducedMotion ? { duration: 0 } : { duration: 0.3, ease: 'easeOut' };
	const wide = useMediaQuery(WIDE_QUERY);
	// The state this mount opened in is already settled: it doesn't pop.
	const [mountPhase] = useState(phase);
	const baseId = useId();
	const titleId = registerFlowTitleId(baseId, agent);

	return (
		<div data-testid="agents-empty-landing" data-phase={phase} className="relative">
			<div className="relative mb-4 flex flex-col gap-4 lg:flex-row lg:gap-0">
				<motion.section
					layout
					transition={{ layout: morph }}
					aria-labelledby={titleId}
					data-testid="first-agent-card"
					// The fleet's agent card surface: `surface-1` with the panel radius
					// and a hairline edge; fields and chips inside step up one tone.
					className="bg-surface-1 border-hairline-field rounded-panel animate-rise relative min-w-0 flex-1 border px-4 pt-4 pb-4 [--field-bg:var(--surface-field)] sm:px-5 sm:pt-5"
				>
					<RegisterFlow
						baseId={baseId}
						agent={agent}
						onApprove={onApprove}
						approvePending={approvePending}
						onDeny={onDeny}
						onExit={onExit}
						registerName={registerName}
						onRegisterNameChange={onRegisterNameChange}
						commandName={commandName}
						registerNameDuplicateOf={registerNameDuplicateOf}
						expectedName={expectedName}
						morePending={morePending}
						onShowFleet={onShowFleet}
						surface="landing"
						reducedMotion={reducedMotion}
						fade={fade}
						morph={morph}
					/>
				</motion.section>

				<AnimatePresence initial={false}>
					{phase === 'listening' && (
						<ManualSlot key="manual" wide={wide} reducedMotion={reducedMotion}>
							<ManualCard onCreateAgent={onCreateAgent} />
						</ManualSlot>
					)}
				</AnimatePresence>
			</div>
			<motion.div layout="position" transition={{ layout: morph }}>
				<GhostFleet
					arrived={
						agent
							? {
									name: agent.name,
									status: agentPhase === 'approved' ? 'active' : 'pending',
								}
							: null
					}
					slotRef={slotRef}
					reducedMotion={reducedMotion}
					settled={phase === mountPhase}
				/>
			</motion.div>
		</div>
	);
}

// ---------------------------------------------------------------------------
// Manual card (secondary)
// ---------------------------------------------------------------------------

/** Side by side from `lg`; stacked below. */
const WIDE_QUERY = '(min-width: 1024px)';
/** The manual column's share of the row, beside the register card. */
const MANUAL_SHARE = '40%';
/** The row gap the manual column brings with it. */
const ROW_GAP_PX = 16;

/**
 * The manual card's column. On a wide row it leaves by sliding right, under
 * the Activity rail (the page shell clips at the rail's edge), while its
 * column narrows to nothing so the register card grows into the row as real
 * layout, frame by frame. The card keeps the width it had, so it slides
 * rather than squeezes. Stacked, it just fades.
 */
function ManualSlot({
	wide,
	reducedMotion,
	children,
}: {
	wide: boolean;
	reducedMotion: boolean;
	children: ReactNode;
}) {
	const ref = useRef<HTMLDivElement>(null);
	const isPresent = useIsPresent();
	const [cardWidth, setCardWidth] = useState<number | null>(null);
	// Measured only while present: an exiting column narrows, and the card
	// riding in it must not.
	useLayoutEffect(() => {
		const el = ref.current;
		if (!el || !isPresent || !wide) return;
		const observer = new ResizeObserver(([entry]) => {
			if (entry) setCardWidth(entry.contentRect.width);
		});
		observer.observe(el);
		return () => observer.disconnect();
	}, [isPresent, wide]);

	const instant = { duration: 0 };
	const slide: Transition = reducedMotion ? instant : { duration: 0.6, ease: EASE_OUT_SOFT };
	// Opacity trails the slide, so the card is seen travelling, not blinking out.
	const fadeOut: Transition = reducedMotion
		? instant
		: { duration: 0.55, ease: [0.55, 0, 0.75, 0.3] };

	if (!wide) {
		return (
			<motion.div
				data-testid="manual-card"
				initial={{ opacity: 0 }}
				animate={{ opacity: 1 }}
				exit={{ opacity: 0, transition: fadeOut }}
				transition={slide}
				className="flex min-w-0"
			>
				{children}
			</motion.div>
		);
	}
	return (
		<motion.div
			ref={ref}
			data-testid="manual-card"
			initial={{ width: 0, marginLeft: 0 }}
			animate={{ width: MANUAL_SHARE, marginLeft: ROW_GAP_PX }}
			exit={{ width: 0, marginLeft: 0 }}
			transition={slide}
			className="relative flex shrink-0"
		>
			<motion.div
				initial={{ x: '115%', opacity: 0 }}
				animate={{ x: 0, opacity: 1 }}
				exit={{ x: '115%', opacity: 0, transition: { x: slide, opacity: fadeOut } }}
				transition={slide}
				style={{ width: cardWidth ?? '100%' }}
				className="flex shrink-0"
			>
				{children}
			</motion.div>
		</motion.div>
	);
}

const MANUAL_STEPS: Array<{
	icon: (props: { className?: string }) => ReactNode;
	title: string;
	detail: string;
}> = [
	{ icon: UserRound, title: 'Name it', detail: 'Created active, able to authenticate' },
	{ icon: KeyRound, title: 'Add APIs & keys', detail: 'Bind a credential to each API' },
	{ icon: McpIcon, title: 'Connect it', detail: 'Hand it an API key, or add Jentic over MCP' },
];

function ManualCard({ onCreateAgent }: { onCreateAgent: () => void }) {
	const headingId = useId();
	return (
		<section
			aria-labelledby={headingId}
			className="bg-surface-1 border-hairline-field rounded-panel animate-rise flex w-full flex-col items-start border px-4 py-4 [--field-bg:var(--surface-field)] [animation-delay:80ms] sm:px-5 sm:py-5"
		>
			<span
				aria-hidden="true"
				className="text-foreground-sub bg-surface-tonal grid h-11 w-11 place-items-center rounded-full"
			>
				<Pencil className="h-[18px] w-[18px]" />
			</span>
			<h2
				id={headingId}
				className="font-heading text-foreground-name mt-3.5 text-[17px] leading-6 font-semibold tracking-[-0.01em]"
			>
				Prefer to set it up yourself?
			</h2>
			<p className="text-muted-foreground mt-1 text-sm leading-normal">
				Create an agent here, add its APIs and keys, then connect it with an API key or MCP.
			</p>
			{/* Raised tonal tiles so the three read as steps in order — lifted off
			    the card by fill alone, no outlines. */}
			<ol className="my-4 grid w-full gap-2">
				{MANUAL_STEPS.map(({ icon: Icon, title, detail }) => (
					<li
						key={title}
						className="border-hairline-field rounded-field grid grid-cols-[28px_1fr] items-center gap-x-3 border px-3.5 py-2.5 text-[13px]"
					>
						<span
							aria-hidden="true"
							className="bg-surface-tonal text-foreground-sub row-span-2 grid h-7 w-7 place-items-center rounded-full"
						>
							<Icon className="h-3.5 w-3.5" />
						</span>
						<span className="text-foreground font-semibold">{title}</span>
						<span className="text-muted-foreground text-xs">{detail}</span>
					</li>
				))}
			</ol>
			<Button variant="secondary" size="sm" onClick={onCreateAgent} className="mt-auto">
				<Plus className="h-4 w-4" />
				Create an agent manually
			</Button>
		</section>
	);
}
