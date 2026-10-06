/**
 * NewAgentPanel — the one "New agent" surface: a right-hand sheet with two
 * routes in, as tabs.
 *
 * - **Create here** (first, and where every opening starts): the manual create
 *   form, whose draft survives a dismissal.
 * - **Register from the CLI**: the same `RegisterFlow` the
 *   zero-agents landing's card renders. It listens for an agent that
 *   registers after the panel opened, then offers its approval, then its first
 *   API; each exit from there closes the panel onto the fleet with the agent
 *   selected (and the Add-APIs flow open when asked). A denied agent sends it
 *   back to listening, the typed name kept. Closed mid-flow, an arrival simply
 *   stays pending in the fleet. The roster is polled for arrivals only while
 *   this tab is on screen.
 *
 * The two tabs split the bar in equal halves. Switching slides the content in
 * the direction of travel (the next tab enters from the right, the previous
 * from the left) while the underline glides across; both panes fill the same
 * box, so the sheet never changes height mid-slide. A click on a tab moves focus
 * to the new pane's first field once it has settled; arrow keys leave focus on
 * the tab list, as the tabs pattern expects. Reduced motion swaps instantly.
 *
 * Each opening starts on "Create here", its name field focused, with a fresh
 * register flow behind the other tab.
 */
import { useEffect, useId, useRef, useState, type ReactNode, type RefObject } from 'react';
import { motion, useReducedMotionConfig, type Transition } from 'framer-motion';
import { X } from 'lucide-react';
import {
	Button,
	SheetPrimitive,
	TabNav,
	type TabNavChangeSource,
	type TabNavOption,
} from '@/shared/ui';
import type { AgentEntity, useApproveAgent, useDenyAgent } from '@/modules/agents/api';
import {
	AgentCreateActions,
	AgentCreateFields,
	useAgentCreateForm,
} from '@/modules/agents/components/AgentCreateForm';
import { EASE_OUT_SOFT } from '@/modules/agents/components/flat/GhostFleet';
import { RegisterFlow } from '@/modules/agents/components/flat/RegisterFlow';
import type { FirstAgentExit } from '@/modules/agents/lib/firstRun';
import { useRegisterPanel } from '@/modules/agents/lib/useRegisterPanel';

export type NewAgentTab = 'register' | 'create';

interface NewAgentPanelProps {
	open: boolean;
	onClose: () => void;
	/** "Create here": the agent just created, and whether to carry on into Add APIs. */
	onCreated: (agent: AgentEntity, opts: { addApis: boolean }) => void;
	/** "Create here": a name to start the draft from. */
	initialName?: string;
	approve: ReturnType<typeof useApproveAgent>;
	deny: Pick<ReturnType<typeof useDenyAgent>, 'variables' | 'isSuccess'>;
	/** Opens the page's deny dialog for the arrival. */
	onDeny: (agent: AgentEntity) => void;
	/** The approved arrival's exit: the panel has closed; show the fleet. */
	onExit: (agent: AgentEntity, exit: FirstAgentExit) => void;
	/** "+N more waiting": the panel has closed; show the fleet's pending agents. */
	onShowFleet: (agent: AgentEntity) => void;
}

/** The tabs in bar order: a switch to a later one travels right. */
const TAB_ORDER: NewAgentTab[] = ['create', 'register'];

/** The tab every opening starts on. */
const INITIAL_TAB: NewAgentTab = 'create';

/**
 * The slide between panes: opacity and translateX only, one ease-out-soft
 * curve for both panes and the tab underline. The distance is a nudge, not the
 * pane's width — a full-width throw covers ~180px a frame and reads as a jolt.
 */
const SLIDE_TRANSITION: Transition = { duration: 0.24, ease: EASE_OUT_SOFT };
const SLIDE_OFFSET_PX = 32;

const FOCUSABLE = 'input:not([disabled]), textarea:not([disabled]), button:not([disabled])';

export function NewAgentPanel({
	open,
	onClose,
	onCreated,
	initialName,
	approve,
	deny,
	onDeny,
	onExit,
	onShowFleet,
}: NewAgentPanelProps) {
	const [tab, setTab] = useState<NewAgentTab>(INITIAL_TAB);
	const [wasOpen, setWasOpen] = useState(open);
	if (open !== wasOpen) {
		setWasOpen(open);
		if (open) setTab(INITIAL_TAB);
	}

	const form = useAgentCreateForm({ open, onClose, onCreated, initialName });
	const register = useRegisterPanel({ open, active: tab === 'register', approve, deny });
	const registerNameRef = useRef<HTMLInputElement>(null);

	const reducedMotion = useReducedMotionConfig() ?? false;
	const fade: Transition = reducedMotion ? { duration: 0 } : { duration: 0.3, ease: 'easeOut' };
	const morph: Transition = reducedMotion
		? { duration: 0 }
		: { duration: 0.56, ease: EASE_OUT_SOFT };

	// The tab whose first field takes focus once its pane has settled (a click
	// asked for it); cleared once focus is placed.
	const focusAfterRef = useRef<NewAgentTab | null>(null);
	const paneRefs = useRef(new Map<NewAgentTab, HTMLDivElement>());
	const firstFieldRef: Record<NewAgentTab, RefObject<HTMLElement | null>> = {
		register: registerNameRef,
		create: form.nameRef,
	};
	function focusFirstField(value: NewAgentTab) {
		if (focusAfterRef.current !== value) return;
		focusAfterRef.current = null;
		const target =
			firstFieldRef[value].current ??
			paneRefs.current.get(value)?.querySelector<HTMLElement>(FOCUSABLE);
		target?.focus({ preventScroll: true });
	}
	// Reduced motion has no slide to wait for: the pane is settled on commit.
	useEffect(() => {
		if (reducedMotion) focusFirstField(tab);
		// eslint-disable-next-line react-hooks/exhaustive-deps -- runs per switch
	}, [tab, reducedMotion]);

	function selectTab(next: NewAgentTab, source: TabNavChangeSource) {
		if (next === tab) return;
		focusAfterRef.current = source === 'click' ? next : null;
		setTab(next);
	}

	const baseId = useId();
	const headingId = `${baseId}-heading`;
	const tabId = (value: NewAgentTab) => `${baseId}-tab-${value}`;
	const panelId = (value: NewAgentTab) => `${baseId}-panel-${value}`;
	const options: TabNavOption<NewAgentTab>[] = [
		{ value: 'create', label: 'Create here' },
		{ value: 'register', label: 'Register from the CLI' },
	];

	const agent = register.agent;
	/** Leave the panel first, so what the host opens next lands on a dismissed sheet. */
	const leave = (then: (a: AgentEntity) => void) => {
		if (!agent) return;
		onClose();
		then(agent);
	};

	const bodies: Record<NewAgentTab, ReactNode> = {
		register: (
			<div className="@container flex-1 overflow-y-auto p-5">
				<RegisterFlow
					baseId={baseId}
					agent={agent}
					onApprove={() => {
						if (agent) approve.mutate(agent.id);
					}}
					approvePending={register.approving}
					onDeny={() => {
						if (agent) onDeny(agent);
					}}
					onExit={(exit) => leave((a) => onExit(a, exit))}
					registerName={register.registerName}
					onRegisterNameChange={register.setRegisterName}
					commandName={register.commandName}
					registerNameDuplicateOf={register.registerNameDuplicateOf}
					expectedName={register.commandName}
					morePending={register.morePending}
					onShowFleet={() => leave(onShowFleet)}
					surface="panel"
					reducedMotion={reducedMotion}
					fade={fade}
					morph={morph}
					nameInputRef={registerNameRef}
				/>
			</div>
		),
		create: (
			<>
				<div className="flex-1 overflow-y-auto p-5">
					<AgentCreateFields form={form} existingNames={register.names} />
				</div>
				<footer className="border-border flex flex-wrap items-center justify-end gap-2 border-t p-5">
					<AgentCreateActions form={form} onCancel={onClose} />
				</footer>
			</>
		),
	};

	return (
		<SheetPrimitive
			open={open}
			onClose={onClose}
			side="right"
			ariaLabelledBy={headingId}
			initialFocus={tab === 'create' ? form.nameRef : registerNameRef}
			// Room for the command and the stepper's four steps in a row; full
			// width on a phone, and about 40% of a wide screen — never under
			// 560px there, where the four steps still fit side by side.
			className="flex flex-col sm:w-[600px] xl:max-w-[max(40vw,560px)]"
		>
			<header className="border-border border-b px-5 pt-4">
				<div className="flex items-start justify-between gap-3">
					<h2 id={headingId} className="text-foreground text-lg font-semibold">
						New agent
					</h2>
					<Button
						variant="ghost"
						size="sm"
						aria-label="Close"
						onClick={onClose}
						className="text-muted-foreground hover:text-foreground -mr-2 shrink-0"
					>
						<X className="h-4 w-4" />
					</Button>
				</div>
				<TabNav
					options={options}
					value={tab}
					onChange={selectTab}
					ariaLabel="How to add the agent"
					getTabId={tabId}
					getControls={panelId}
					fill
					className="mt-2 border-b-0"
				/>
			</header>

			{/* Both panes stay mounted, stacked in this one box: a switch only
			    moves them (no remount, and so no dropped frames), and the box's
			    height never changes. */}
			<div className="relative min-h-0 flex-1 overflow-hidden">
				{TAB_ORDER.map((value, index) => {
					const active = value === tab;
					// An inactive pane waits on the side it sits in the bar.
					const side = index < TAB_ORDER.indexOf(tab) ? -1 : 1;
					return (
						<TabPane
							key={value}
							id={panelId(value)}
							labelledBy={tabId(value)}
							testId={`new-agent-panel-${value}`}
							phase={value === 'register' ? register.phase : undefined}
							active={active}
							offset={active ? 0 : side * SLIDE_OFFSET_PX}
							reducedMotion={reducedMotion}
							paneRef={(el) => {
								if (el) paneRefs.current.set(value, el);
								else paneRefs.current.delete(value);
							}}
							onSettled={() => focusFirstField(value)}
						>
							{bodies[value]}
						</TabPane>
					);
				})}
			</div>
		</SheetPrimitive>
	);
}

/**
 * One tab's pane. The active one sits at rest; an inactive one fades out a
 * nudge to its side and is then hidden, inert and out of the accessibility
 * tree, so only the active pane can be reached.
 */
function TabPane({
	id,
	labelledBy,
	testId,
	phase,
	active,
	offset,
	reducedMotion,
	paneRef,
	onSettled,
	children,
}: {
	id: string;
	labelledBy: string;
	testId: string;
	phase?: string;
	active: boolean;
	/** Where the pane rests, in px: 0 when active, ± the nudge when not. */
	offset: number;
	reducedMotion: boolean;
	paneRef: (el: HTMLDivElement | null) => void;
	/** The pane has finished sliding in. */
	onSettled: () => void;
	children: ReactNode;
}) {
	// `will-change` only while moving, so a resting pane holds no layer.
	const [moving, setMoving] = useState(false);
	return (
		<motion.div
			ref={paneRef}
			role="tabpanel"
			id={id}
			aria-labelledby={labelledBy}
			aria-hidden={active ? undefined : true}
			inert={!active}
			data-testid={testId}
			data-phase={phase}
			data-state={active ? 'active' : 'inactive'}
			initial={false}
			animate={
				reducedMotion
					? { x: offset, opacity: active ? 1 : 0 }
					: active
						? { x: 0, opacity: 1, visibility: 'visible' }
						: { x: offset, opacity: 0, transitionEnd: { visibility: 'hidden' } }
			}
			transition={reducedMotion ? { duration: 0 } : SLIDE_TRANSITION}
			onAnimationStart={() => setMoving(true)}
			onAnimationComplete={() => {
				setMoving(false);
				if (active && !reducedMotion) onSettled();
			}}
			style={{
				willChange: moving ? 'transform, opacity' : undefined,
				// Reduced motion swaps in the commit itself, so focus can follow at once.
				...(reducedMotion && { visibility: active ? 'visible' : 'hidden' }),
			}}
			className="absolute inset-0 flex flex-col"
		>
			{children}
		</motion.div>
	);
}
