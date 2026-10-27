/**
 * NewAgentPanel — the "New agent" sheet: a "Connect with" rail (CLI, MCP, create
 * here) beside the chosen route's pane. Both register routes share one listener,
 * and the roster is polled for arrivals only while one is on screen. A finished
 * agent gets its first API here (`offerFirstApi`) or hands the sheet to the
 * Add-APIs tray. "Create it here" needs `agents:write` (or `org:admin`); without
 * it only the register routes show, whose agent lands pending.
 */
import {
	useEffect,
	useLayoutEffect,
	useId,
	useRef,
	useState,
	type KeyboardEvent,
	type ReactNode,
	type RefObject,
} from 'react';
import { motion, useReducedMotionConfig, type Transition } from 'framer-motion';
import { Pencil, RefreshCw, Terminal, X } from 'lucide-react';
import { Button, McpIcon, SheetFooter, SheetPrimitive, holdSheetForHandoff } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import { AGENTS_WRITE, useCanAccess } from '@/shared/auth';
import type { AgentEntity, useApproveAgent, useDenyAgent } from '@/modules/agents/api';
import {
	AgentCreateActions,
	AgentCreateFields,
	useAgentCreateForm,
} from '@/modules/agents/components/AgentCreateForm';
import { AgentNameText } from '@/modules/agents/components/AgentNameText';
import { EASE_OUT_SOFT } from '@/modules/agents/components/flat/GhostFleet';
import { RegisterFlow } from '@/modules/agents/components/flat/RegisterFlow';
import { AgentDetails, CardHeader } from '@/modules/agents/components/flat/firstAgentParts';
import type { FirstAgentExit } from '@/modules/agents/lib/firstRun';
import { AGENT_SETUP_FLOW } from '@/modules/agents/lib/setupQueue';
import { useRegisterPanel } from '@/modules/agents/lib/useRegisterPanel';

export type NewAgentTab = 'register' | 'mcp' | 'create';

interface NewAgentPanelProps {
	open: boolean;
	onClose: () => void;
	/** Whether a finished agent gets its first API here, or goes to the Add-APIs tray. */
	offerFirstApi: boolean;
	/** The panel has closed, holding its sheet for the tray that opens for `agent`. */
	onToAddApis: (agent: AgentEntity) => void;
	approve: ReturnType<typeof useApproveAgent>;
	deny: Pick<ReturnType<typeof useDenyAgent>, 'variables' | 'isSuccess'>;
	/** Opens the page's deny dialog for the arrival. */
	onDeny: (agent: AgentEntity) => void;
	/** The first-API choice was made and the panel has closed. */
	onExit: (agent: AgentEntity, exit: FirstAgentExit) => void;
	/** "+N more waiting": the panel has closed; show the fleet's pending agents. */
	onShowFleet: (agent: AgentEntity) => void;
	initialTab?: NewAgentTab;
}

const TAB_ORDER: NewAgentTab[] = ['register', 'mcp', 'create'];

const INITIAL_TAB: NewAgentTab = 'register';

/** Both register routes share one pane (and its one listener). */
type Pane = 'register' | 'create';
const paneOf = (tab: NewAgentTab): Pane => (tab === 'create' ? 'create' : 'register');

const RAIL: Record<
	NewAgentTab,
	{ label: string; short: string; icon: (p: { className?: string }) => ReactNode }
> = {
	register: { label: 'Register from the CLI', short: 'CLI', icon: Terminal },
	mcp: { label: 'Connect over MCP', short: 'MCP', icon: McpIcon },
	create: { label: 'Create it here', short: 'Create', icon: Pencil },
};

/** A nudge, not the pane's height: a full throw reads as a jolt. */
const SLIDE_TRANSITION: Transition = { duration: 0.24, ease: EASE_OUT_SOFT };
const SLIDE_OFFSET_PX = 18;

const FOCUSABLE = 'input:not([disabled]), textarea:not([disabled]), button:not([disabled])';

export function NewAgentPanel({
	open,
	onClose,
	offerFirstApi,
	onToAddApis,
	approve,
	deny,
	onDeny,
	onExit,
	onShowFleet,
	initialTab = INITIAL_TAB,
}: NewAgentPanelProps) {
	const canCreate = useCanAccess(AGENTS_WRITE);
	const [chosenTab, setTab] = useState<NewAgentTab>(initialTab);
	// The agent "Create" made in this opening; the roster's copy, once it has one, updates it.
	const [created, setCreated] = useState<AgentEntity | null>(null);
	const [wasOpen, setWasOpen] = useState(open);
	if (open !== wasOpen) {
		setWasOpen(open);
		if (open) {
			setTab(initialTab);
			setCreated(null);
		}
	}
	const tab: NewAgentTab = canCreate || chosenTab !== 'create' ? chosenTab : 'register';
	const tabOrder = canCreate ? TAB_ORDER : TAB_ORDER.filter((t) => t !== 'create');
	const paneOrder: Pane[] = canCreate ? ['register', 'create'] : ['register'];
	const pane = paneOf(tab);

	const toAddApis = (agent: AgentEntity) => {
		holdSheetForHandoff(AGENT_SETUP_FLOW);
		onClose();
		onToAddApis(agent);
	};

	const form = useAgentCreateForm({
		open,
		onCreated: (agent) => {
			if (offerFirstApi || agent.status === 'pending') setCreated(agent);
			else toAddApis(agent);
		},
	});
	const register = useRegisterPanel({
		open,
		active: pane === 'register',
		approve,
		deny,
		excludeId: created?.id ?? null,
	});
	const registerNameRef = useRef<HTMLInputElement>(null);
	const createdLive = created
		? (register.agents.find((a) => a.id === created.id) ?? created)
		: null;
	// Denied (here or elsewhere) or archived: nothing left to finish, so the form is back.
	const createdDenied = created != null && deny.isSuccess && deny.variables?.id === created.id;
	const createdAgent =
		createdLive &&
		!createdDenied &&
		(createdLive.status === 'active' || createdLive.status === 'pending')
			? createdLive
			: null;

	const reducedMotion = useReducedMotionConfig() ?? false;
	const fade: Transition = reducedMotion ? { duration: 0 } : { duration: 0.3, ease: 'easeOut' };
	const morph: Transition = reducedMotion
		? { duration: 0 }
		: { duration: 0.56, ease: EASE_OUT_SOFT };

	// The pane whose first field takes focus once it has settled (a click
	// asked for it); cleared once focus is placed.
	const focusAfterRef = useRef<Pane | null>(null);
	const paneRefs = useRef(new Map<Pane, HTMLDivElement>());
	const firstFieldRef: Record<Pane, RefObject<HTMLElement | null>> = {
		register: registerNameRef,
		create: form.nameRef,
	};
	function focusFirstField(value: Pane) {
		if (focusAfterRef.current !== value) return;
		focusAfterRef.current = null;
		const target =
			firstFieldRef[value].current ??
			paneRefs.current.get(value)?.querySelector<HTMLElement>(FOCUSABLE);
		target?.focus({ preventScroll: true });
	}
	// Reduced motion, or CLI ↔ MCP (one pane), has no slide to wait for.
	const focusNowRef = useRef(false);
	useEffect(() => {
		if (reducedMotion || focusNowRef.current) focusFirstField(pane);
		focusNowRef.current = false;
		// eslint-disable-next-line react-hooks/exhaustive-deps -- runs per switch
	}, [tab, reducedMotion]);

	function selectTab(next: NewAgentTab, source: 'click' | 'arrow') {
		if (next === tab) return;
		focusAfterRef.current = source === 'click' ? paneOf(next) : null;
		focusNowRef.current = paneOf(next) === pane;
		setTab(next);
	}

	const baseId = useId();
	const headingId = `${baseId}-heading`;
	const tabId = (value: NewAgentTab) => `${baseId}-tab-${value}`;
	const panelId = (value: Pane) => `${baseId}-panel-${value}`;

	const agent = register.agent;
	/** Close first, so what the host opens next lands on a dismissed sheet. */
	const leave = (
		then: (a: AgentEntity) => void,
		carriesOn = false,
		subject: AgentEntity | null = agent,
	) => {
		if (!subject) return;
		if (carriesOn) holdSheetForHandoff(AGENT_SETUP_FLOW);
		onClose();
		then(subject);
	};
	const exitWith = (subject: AgentEntity | null, exit: FirstAgentExit) =>
		leave((a) => onExit(a, exit), exit.kind !== 'skip', subject);
	/** Once "Create" has made an agent, closing answers its first API as "Skip for now". */
	const close = () => {
		if (createdAgent) exitWith(createdAgent, { kind: 'skip' });
		else onClose();
	};

	// The created view replaces the button that made it, so focus moves to its heading.
	const createdTitleId = `${baseId}-created`;
	const createdId = createdAgent?.id ?? null;
	useEffect(() => {
		if (createdId) document.getElementById(createdTitleId)?.focus({ preventScroll: true });
	}, [createdId, createdTitleId]);
	// Over a fleet, an approval this panel asked for hands off to the tray as it lands.
	const finished =
		agent?.status === 'active'
			? agent
			: createdAgent?.status === 'active'
				? createdAgent
				: null;
	const approvedHere = finished != null && approve.isSuccess && approve.variables === finished.id;
	// Held on its approval until the tray takes over: the first-API step never paints.
	const handingOff = !offerFirstApi && approvedHere ? finished : null;
	const holdOnApproval = (a: AgentEntity | null): AgentEntity | null =>
		a && handingOff?.id === a.id ? { ...a, status: 'pending' } : a;
	const handedRef = useRef<string | null>(null);
	useLayoutEffect(() => {
		if (offerFirstApi || !open || !approvedHere || !finished) return;
		if (handedRef.current === finished.id) return;
		handedRef.current = finished.id;
		toAddApis(finished);
		// eslint-disable-next-line react-hooks/exhaustive-deps -- once per approval
	}, [offerFirstApi, open, approvedHere, finished?.id]);
	const createdApproving =
		createdAgent != null &&
		approve.variables === createdAgent.id &&
		(approve.isPending || (approve.isSuccess && createdAgent.status === 'pending'));

	// An arrival doesn't say which command it ran: the live line sits on the last route shown.
	const [lastRoute, setLastRoute] = useState<'register' | 'mcp'>(
		initialTab === 'mcp' ? 'mcp' : 'register',
	);
	if (tab !== 'create' && tab !== lastRoute) setLastRoute(tab);
	const liveStatus: RailStatus | null = agent
		? {
				tone: agent.status === 'pending' ? 'pending' : 'active',
				text: (
					<>
						<AgentNameText name={agent.name} className="font-mono" /> ·{' '}
						{agent.status === 'pending' ? 'pending' : 'approved'}
					</>
				),
			}
		: pane === 'register' && register.listening
			? { tone: 'listening', text: 'Listening…' }
			: null;
	const draftName = form.name.trim();
	const status: Record<NewAgentTab, RailStatus> = {
		register: (lastRoute === 'register' && liveStatus) || {
			tone: 'idle',
			text: 'Needs the jentic CLI',
		},
		mcp: (lastRoute === 'mcp' && liveStatus) || {
			tone: 'idle',
			text: 'Cursor · Claude · Codex',
		},
		create: createdAgent
			? {
					tone: createdAgent.status === 'pending' ? 'pending' : 'active',
					text: (
						<>
							<AgentNameText name={createdAgent.name} className="font-mono" /> ·{' '}
							{createdAgent.status === 'pending' ? 'pending' : 'created'}
						</>
					),
				}
			: draftName
				? {
						tone: 'idle',
						text: (
							<>
								Draft · <AgentNameText name={draftName} className="font-mono" />
							</>
						),
					}
				: { tone: 'idle', text: 'Nothing to install' },
	};

	const route = tab === 'mcp' ? 'mcp' : 'cli';
	const [checking, setChecking] = useState(false);
	// `refetch` resolves on failure too, so the spinner always clears.
	const checkNow = async () => {
		setChecking(true);
		await Promise.all([register.checkNow(), new Promise((r) => setTimeout(r, 500))]);
		setChecking(false);
	};
	const bodies: Record<Pane, ReactNode> = {
		register: (
			<>
				<div className="@container flex-1 [scrollbar-gutter:stable] overflow-y-auto p-5 md:px-6">
					<motion.div
						key={route}
						initial={{ opacity: 0 }}
						animate={{ opacity: 1 }}
						transition={fade}
					>
						<RegisterFlow
							baseId={`${baseId}-${route}`}
							agent={holdOnApproval(agent)}
							onApprove={() => {
								if (agent) approve.mutate(agent.id);
							}}
							approvePending={
								register.approving || (agent != null && handingOff?.id === agent.id)
							}
							onDeny={() => {
								if (agent) onDeny(agent);
							}}
							onExit={(exit) => exitWith(agent, exit)}
							registerName={register.registerName}
							onRegisterNameChange={register.setRegisterName}
							commandName={register.commandName}
							registerNameDuplicateOf={register.registerNameDuplicateOf}
							expectedName={register.commandName}
							morePending={register.morePending}
							onShowFleet={() => leave(onShowFleet)}
							surface="panel"
							route={route}
							reducedMotion={reducedMotion}
							fade={fade}
							morph={morph}
							nameInputRef={registerNameRef}
						/>
					</motion.div>
				</div>
				<SheetFooter className="flex-wrap">
					<Button
						variant="ghost"
						onClick={close}
						// Phones close from the header.
						className={agent ? undefined : 'hidden sm:inline-flex'}
					>
						{agent ? 'Close' : 'Cancel'}
					</Button>
					{!agent && (
						<Button
							variant="secondary"
							onClick={() => void checkNow()}
							loading={checking}
							data-testid="footer-check-now"
						>
							{!checking && <RefreshCw className="h-4 w-4" />}
							{checking ? 'Checking…' : 'Check now'}
						</Button>
					)}
				</SheetFooter>
			</>
		),
		create: (
			<>
				<div className="flex-1 [scrollbar-gutter:stable] overflow-y-auto p-5 md:px-6">
					<motion.div
						key={createdAgent ? `created-${createdAgent.id}` : 'form'}
						initial={{ opacity: 0 }}
						animate={{ opacity: 1 }}
						transition={fade}
					>
						{createdAgent ? (
							<AgentDetails
								titleId={createdTitleId}
								agent={holdOnApproval(createdAgent) ?? createdAgent}
								phase={
									holdOnApproval(createdAgent)?.status === 'pending'
										? 'arrived'
										: 'approved'
								}
								onApprove={() => approve.mutate(createdAgent.id)}
								approvePending={
									createdApproving || handingOff?.id === createdAgent.id
								}
								onDeny={() => onDeny(createdAgent)}
								onExit={(exit) => exitWith(createdAgent, exit)}
								fade={fade}
								expectedName={null}
								morePending={0}
								onShowFleet={() => leave(onShowFleet, false, createdAgent)}
								progress={false}
							/>
						) : (
							<>
								<CardHeader
									titleId={`${baseId}-create-title`}
									glyph={
										<span
											aria-hidden="true"
											className="text-primary bg-primary/10 grid h-11 w-11 shrink-0 place-items-center rounded-full"
										>
											<Pencil className="h-[18px] w-[18px]" />
										</span>
									}
									title="Create it here"
									detail="Nothing to install. It's created active and can sign in at once — next you give it its APIs, then hand it an API key or connect it over MCP."
								/>
								<div className="mt-5">
									<AgentCreateFields form={form} existingNames={register.names} />
								</div>
							</>
						)}
					</motion.div>
				</div>
				<SheetFooter className="flex-wrap">
					{createdAgent ? (
						<Button variant="ghost" onClick={close}>
							Close
						</Button>
					) : (
						<AgentCreateActions form={form} onCancel={onClose} />
					)}
				</SheetFooter>
			</>
		),
	};

	return (
		<SheetPrimitive
			open={open}
			onClose={close}
			side="right"
			ariaLabelledBy={headingId}
			initialFocus={pane === 'create' ? form.nameRef : registerNameRef}
			// The setup flow's one width, kept by the tray and queue that follow.
			size="flow"
			handoffKey={AGENT_SETUP_FLOW}
			className="flex flex-col"
		>
			{/* A div, not <header>: in a sheet it would read as a second banner. */}
			<div className="flex items-start justify-between gap-3 px-5 pt-4 pb-3">
				<h2
					id={headingId}
					className="font-heading text-foreground-name text-lg font-semibold"
				>
					New agent
				</h2>
				<Button
					variant="ghost"
					size="sm"
					aria-label="Close"
					onClick={close}
					className="text-muted-foreground hover:text-foreground -mr-2 shrink-0"
				>
					<X className="h-4 w-4" />
				</Button>
			</div>

			<div className="flex min-h-0 flex-1 flex-col md:flex-row">
				<Rail
					tabs={tabOrder}
					value={tab}
					onChange={selectTab}
					status={status}
					tabId={tabId}
					controls={(value) => panelId(paneOf(value))}
				/>
				{/* Both panes stay mounted and stacked, so a switch never remounts or resizes. */}
				<div className="relative min-h-0 flex-1 overflow-hidden">
					{paneOrder.map((value, index) => {
						const active = value === pane;
						const side = index < paneOrder.indexOf(pane) ? -1 : 1;
						return (
							<TabPane
								key={value}
								id={panelId(value)}
								labelledBy={tabId(
									value === 'register' && tab === 'mcp' ? 'mcp' : value,
								)}
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
			</div>
		</SheetPrimitive>
	);
}

type RailStatus = { tone: 'listening' | 'pending' | 'active' | 'idle'; text: ReactNode };

/** "Connect with" tablist: arrow keys (either axis), Home and End move focus and selection. */
function Rail({
	tabs,
	value,
	onChange,
	status,
	tabId,
	controls,
}: {
	tabs: NewAgentTab[];
	value: NewAgentTab;
	onChange: (value: NewAgentTab, source: 'click' | 'arrow') => void;
	status: Record<NewAgentTab, RailStatus>;
	tabId: (value: NewAgentTab) => string;
	controls: (value: NewAgentTab) => string;
}) {
	const refs = useRef(new Map<NewAgentTab, HTMLButtonElement>());
	function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
		const i = tabs.indexOf(value);
		const n = tabs.length;
		const next =
			e.key === 'ArrowDown' || e.key === 'ArrowRight'
				? (i + 1) % n
				: e.key === 'ArrowUp' || e.key === 'ArrowLeft'
					? (i - 1 + n) % n
					: e.key === 'Home'
						? 0
						: e.key === 'End'
							? n - 1
							: null;
		if (next == null) return;
		e.preventDefault();
		onChange(tabs[next], 'arrow');
		refs.current.get(tabs[next])?.focus();
	}
	return (
		<div className="bg-surface-sheet-foot w-full shrink-0 px-3 py-2 md:w-[248px] md:py-3">
			<p
				aria-hidden="true"
				className="text-foreground-faint hidden px-2.5 pb-2 text-[11px] font-semibold tracking-wider uppercase md:block"
			>
				Connect with
			</p>
			<div
				role="tablist"
				aria-label="How to add the agent"
				aria-orientation="vertical"
				onKeyDown={onKeyDown}
				className="flex gap-1 md:flex-col"
			>
				{tabs.map((t) => {
					const { label, short, icon: Icon } = RAIL[t];
					const selected = t === value;
					const s = status[t];
					return (
						<Button
							key={t}
							ref={(el) => {
								if (el) refs.current.set(t, el);
								else refs.current.delete(t);
							}}
							variant="ghost"
							role="tab"
							id={tabId(t)}
							aria-selected={selected}
							aria-controls={controls(t)}
							aria-describedby={`${tabId(t)}-status`}
							tabIndex={selected ? 0 : -1}
							data-testid={`new-agent-rail-${t}`}
							onClick={() => onChange(t, 'click')}
							className={cn(
								'h-auto min-w-0 flex-1 justify-center gap-3 rounded-lg px-2 py-2 text-left font-normal md:w-full md:flex-none md:justify-start md:px-2.5 md:py-2.5',
								selected
									? 'bg-surface-selected hover:bg-surface-selected'
									: 'hover:bg-tint-2',
							)}
						>
							<span
								aria-hidden="true"
								className={cn(
									'hidden h-8 w-8 shrink-0 place-items-center rounded-lg transition-colors duration-200 md:grid',
									selected
										? 'bg-primary/10 text-primary'
										: 'text-muted-foreground',
								)}
							>
								<Icon className="h-4 w-4" />
							</span>
							<span className="min-w-0 md:flex-1">
								<span
									className={cn(
										'block truncate text-sm font-semibold',
										selected ? 'text-foreground-name' : 'text-foreground-sub',
									)}
								>
									<span aria-hidden="true" className="md:hidden">
										{short}
									</span>
									<span className="sr-only md:not-sr-only">{label}</span>
								</span>
								<span
									id={`${tabId(t)}-status`}
									aria-hidden="true"
									data-testid={`new-agent-rail-status-${t}`}
									className="text-muted-foreground mt-0.5 hidden min-w-0 items-center gap-1.5 text-xs md:flex"
								>
									<span
										aria-hidden="true"
										className={cn(
											'h-1.5 w-1.5 shrink-0 rounded-full transition-colors duration-300',
											s.tone === 'listening' &&
												'bg-primary/80 animate-soft-pulse',
											s.tone === 'pending' && 'bg-warning',
											s.tone === 'active' && 'bg-success/80',
											s.tone === 'idle' && 'bg-foreground-faint/40',
										)}
									/>
									<span className="truncate">{s.text}</span>
								</span>
							</span>
						</Button>
					);
				})}
			</div>
		</div>
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
					? { y: offset, opacity: active ? 1 : 0 }
					: active
						? { y: 0, opacity: 1, visibility: 'visible' }
						: { y: offset, opacity: 0, transitionEnd: { visibility: 'hidden' } }
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
