/**
 * FirstAgentLanding — the Agents page with no agents in the org yet, and the
 * place its first self-registered agent is finished.
 *
 * One "Connect your first agent" card whose Continue opens the New agent panel
 * on the chosen way in, over a faint preview of the fleet view.
 *
 * The parent polls (and the event stream invalidates) the agents list while
 * this is on screen and passes the registered agent in as `agent`. The card
 * follows that agent's status, not a click here, so an approval from the
 * Activity rail or the bell lands the same way:
 *  - pending: the ways in give way to the agent's details with Approve /
 *    Deny, and the progress marks steps 1–2 done.
 *  - active: steps 1–3 are done and "Give it an API" is current, as the
 *    actions give way to the first-API suggestion (GitHub when the workspace
 *    or catalog has it).
 * Each choice there goes to `onExit`, and the parent swaps in the fleet view.
 * A denied agent drops out of `agent`, and the card is back to listening.
 * Reduced motion swaps each state at once.
 *
 * On a reload the parent may mount this straight into `arrived` or `approved`
 * (it resumes from the org's data). That state is simply rendered; only
 * in-session changes animate and move focus to the card's new heading.
 */
import { useEffect, useId, useRef, useState, type ReactNode, type RefObject } from 'react';
import { AnimatePresence, motion, useReducedMotionConfig, type Transition } from 'framer-motion';
import { useNavigate } from 'react-router';
import { ArrowRight, LibraryBig, Pencil, Terminal, Upload, Wallet } from 'lucide-react';
import { AppLink, Button, McpIcon, RadioCardGroup } from '@/shared/ui';
import { ROUTES, ROUTE_PATHS } from '@/shared/app/routes';
import { ImportSpecDialog } from '@/shared/credentials/components';
import { cn } from '@/shared/lib/utils';
import { AGENTS_WRITE, useCanAccess } from '@/shared/auth';
import type { AgentEntity } from '@/modules/agents/api';
import { EASE_OUT_SOFT, GhostFleet } from '@/modules/agents/components/flat/GhostFleet';
import { CommandBlock } from '@/modules/agents/components/flat/firstAgentParts';
import type { NewAgentTab } from '@/modules/agents/components/flat/NewAgentPanel';
import { RegisterFlow, registerFlowTitleId } from '@/modules/agents/components/flat/RegisterFlow';
import type { FirstAgentExit, FirstAgentPhase } from '@/modules/agents/lib/firstRun';
import { registerCommandTokens, type CommandToken } from '@/modules/agents/lib/registerCommand';
import { useRegisterTarget } from '@/modules/agents/lib/useRegisterTarget';

interface FirstAgentLandingProps {
	/** Opens the New agent panel on `tab`. */
	onCreateAgent: (tab: NewAgentTab) => void;
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
	// The state this mount opened in is already settled: it doesn't pop.
	const [mountPhase] = useState(phase);
	const baseId = useId();
	const listening = agent == null;
	const titleId = registerFlowTitleId(baseId, agent);

	// A swap to or from the ways in focuses the title, after any open deny dialog closes.
	const lastListening = useRef(listening);
	useEffect(() => {
		if (lastListening.current === listening) return;
		lastListening.current = listening;
		const focusTitle = () => document.getElementById(titleId)?.focus({ preventScroll: true });
		const modal = document.querySelector('dialog[open]');
		if (!modal) return focusTitle();
		const onModalClose = () => requestAnimationFrame(focusTitle);
		modal.addEventListener('close', onModalClose, { once: true });
		return () => modal.removeEventListener('close', onModalClose);
	}, [listening, titleId]);

	return (
		<div data-testid="agents-empty-landing" data-phase={phase} className="relative flow-root">
			<div className="absolute inset-x-0 top-0">
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
			</div>
			<section
				aria-labelledby={titleId}
				data-testid="first-agent-card"
				// The top margin seats the card's edge in the ghost frame's clear band; it stays
				// near-constant because the frame above is a fixed pixel stack.
				className="bg-surface-1 rounded-panel animate-rise relative z-[1] mx-auto mt-[clamp(4rem,8vh,4.5rem)] max-w-[960px] overflow-hidden [--field-bg:var(--surface-field)]"
			>
				<AnimatePresence mode="popLayout" initial={false}>
					<motion.div
						key={listening ? 'ways-in' : 'agent'}
						initial={{ opacity: 0 }}
						animate={{ opacity: 1 }}
						exit={{ opacity: 0 }}
						transition={fade}
						className={listening ? undefined : 'px-4 py-4 sm:px-7 sm:py-6'}
					>
						{listening ? (
							<WaysIn
								titleId={titleId}
								commandName={commandName}
								onContinue={onCreateAgent}
							/>
						) : (
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
						)}
					</motion.div>
				</AnimatePresence>
			</section>
			<Shortcuts />
		</div>
	);
}

// ---------------------------------------------------------------------------
// The ways in
// ---------------------------------------------------------------------------

type Method = 'cli' | 'mcp' | 'create';
type Icon = (p: { className?: string }) => ReactNode;

const METHODS: Record<Method, { icon: Icon; label: string; meta: string; tab: NewAgentTab }> = {
	cli: {
		icon: Terminal,
		label: 'Register from the CLI',
		meta: 'Needs the jentic CLI',
		tab: 'register',
	},
	mcp: {
		icon: McpIcon,
		label: 'Connect over MCP',
		meta: 'Cursor · Claude · any client',
		tab: 'mcp',
	},
	create: { icon: Pencil, label: 'Create it here', meta: 'Nothing to install', tab: 'create' },
};

const CODE = 'bg-surface-tonal/60 rounded-lg font-mono text-xs leading-relaxed';

function WaysIn({
	titleId,
	commandName,
	onContinue,
}: {
	titleId: string;
	commandName: string;
	onContinue: (tab: NewAgentTab) => void;
}) {
	const canCreate = useCanAccess(AGENTS_WRITE);
	const methods: Method[] = canCreate ? ['cli', 'mcp', 'create'] : ['cli', 'mcp'];
	const [method, setMethod] = useState<Method>('cli');
	const target = useRegisterTarget();
	// One token list per verb, so the display and the clipboard get the same words.
	const registerTokens = registerCommandTokens({ ...target, name: commandName });
	const setupTokens = registerCommandTokens({ ...target, name: commandName, verb: 'setup' });
	const copy: Record<Method, { lead: string; tokens?: CommandToken[]; verb?: string }> = {
		cli: {
			lead: 'Run one command where your agent runs.',
			tokens: registerTokens,
			verb: 'register',
		},
		mcp: {
			lead: 'For Cursor, Claude Code, Claude Desktop or Codex, run setup where your agent runs.',
			tokens: setupTokens,
			verb: 'setup',
		},
		create: {
			lead: 'Set it up in Jentic, with nothing to install.',
		},
	};
	return (
		<>
			<div className="px-5 pt-6 sm:px-7">
				<span className="text-foreground-sub bg-surface-tonal/70 mb-3 inline-flex items-center gap-2 rounded-full py-1 pr-3 pl-1.5 text-xs font-semibold">
					<span
						aria-hidden="true"
						className="size-4 rounded-full border-[1.5px] border-dashed border-[hsl(var(--control-edge)/0.7)]"
					/>
					0 agents
				</span>
				<h2
					id={titleId}
					tabIndex={-1}
					className="font-heading text-foreground-name text-base leading-6 font-semibold tracking-[-0.01em] outline-none"
				>
					Connect your first agent
				</h2>
				<p
					role="status"
					aria-live="polite"
					data-testid="register-status"
					className="sr-only"
				>
					Listening for new agents…
				</p>
			</div>
			<div className="grid gap-5 px-5 pt-4 pb-6 sm:px-7 md:grid-cols-[300px_minmax(0,1fr)]">
				<div className="grid content-start gap-1.5">
					<p className="text-foreground-faint pb-1 pl-1 text-[10.5px] leading-none font-bold tracking-[0.09em] uppercase">
						Connect with
					</p>
					<RadioCardGroup<Method>
						ariaLabel="How to connect your first agent"
						value={method}
						onChange={setMethod}
						options={methods.map((m) => {
							const { icon: Icon, label, meta } = METHODS[m];
							const on = m === method;
							const tone = on
								? 'bg-primary/10 text-primary'
								: 'bg-surface-tonal text-foreground-sub';
							return {
								value: m,
								label,
								description: meta,
								leading: (
									<span
										className={cn(
											'grid size-10 place-items-center rounded-full',
											tone,
										)}
									>
										<Icon className="h-[18px] w-[18px]" />
									</span>
								),
							};
						})}
						className="[&>[role=radio][aria-checked=true]]:bg-surface-selected [&>[role=radio][aria-checked=false]>span:last-child]:bg-surface-tonal grid gap-1.5 divide-y-0 bg-transparent [&>[role=radio]]:rounded-lg [&>[role=radio]]:p-3 [&>[role=radio]>span:last-child]:border-0"
					/>
				</div>
				<div
					data-testid="landing-preview"
					data-method={method}
					className="bg-field flex min-w-0 flex-col rounded-xl"
				>
					{/* One grid cell for every pane, so the well keeps the tallest's height. */}
					<div className="grid px-5 pt-5">
						{methods.map((m) => {
							const c = copy[m];
							return (
								<motion.div
									key={m}
									data-testid={`landing-pane-${m}`}
									aria-hidden={m !== method || undefined}
									inert={m !== method}
									initial={false}
									animate={
										m === method ? { opacity: 1, x: 0 } : { opacity: 0, x: 8 }
									}
									transition={{ duration: 0.2, ease: EASE_OUT_SOFT }}
									className={cn('[grid-area:1/1]', m !== method && 'invisible')}
								>
									<h3 className="text-foreground-name font-heading text-sm font-semibold tracking-[-0.01em]">
										{METHODS[m].label}
									</h3>
									<p className="text-muted-foreground mt-1 text-sm leading-normal">
										{c.lead}
									</p>
									{c.tokens && c.verb && (
										<div className="mt-3">
											<CommandBlock
												tokens={c.tokens}
												verb={c.verb}
												testIdPrefix={
													m === 'cli'
														? 'register-command'
														: 'setup-command'
												}
											/>
										</div>
									)}
									{m === 'cli' &&
										target.backend === 'remote' &&
										!target.brokerUrl && (
											<p
												data-testid="register-broker-note"
												className="text-muted-foreground mt-2 text-xs"
											>
												Ask whoever deployed this instance for the broker
												URL, and put it in place of{' '}
												<code className="font-mono">
													&lt;broker-url&gt;
												</code>
												.
											</p>
										)}
									{m === 'create' && (
										<ul className="text-foreground-sub mt-3 grid gap-2 text-sm">
											{[
												['Name', 'and an optional description'],
												['Permissions', 'optional, to start it with'],
												[
													'APIs',
													'and the credential each one calls through',
												],
											].map(([field, detail]) => (
												<li
													key={field}
													className={cn(CODE, 'px-3 py-2 font-sans')}
												>
													<b className="text-foreground font-semibold">
														{field}
													</b>{' '}
													<span className="text-muted-foreground">
														{detail}
													</span>
												</li>
											))}
										</ul>
									)}
								</motion.div>
							);
						})}
					</div>
					<div className="mt-auto flex flex-col items-start gap-3 px-5 pt-4 pb-4 sm:flex-row sm:items-center sm:justify-between">
						<span className="text-foreground-faint text-xs">
							Opens the New agent panel
						</span>
						<Button onClick={() => onContinue(METHODS[method].tab)}>
							{method === 'create'
								? 'Continue'
								: `Continue with ${method.toUpperCase()}`}
							<ArrowRight className="h-4 w-4" />
						</Button>
					</div>
				</div>
			</div>
		</>
	);
}

// ---------------------------------------------------------------------------
// Shortcuts under the card
// ---------------------------------------------------------------------------

const TILE =
	'group bg-surface-1 hover:bg-surface-tonal focus-visible:bg-surface-tonal rounded-panel flex items-center gap-3 px-3 py-2.5 text-left transition-[background-color] duration-[160ms] ease-(--ease-out-soft)';

function Shortcuts() {
	const [uploadOpen, setUploadOpen] = useState(false);
	const navigate = useNavigate();
	// Built at render: `routes` is still initialising when this module loads.
	const shortcuts: [Icon, string, string, string | null][] = [
		[LibraryBig, 'Browse the API catalog', 'See what your agent could call', ROUTES.library],
		[
			Wallet,
			'Add a credential',
			'Store a key now, bind it later',
			ROUTE_PATHS.credentialInventory({ create: true }),
		],
		[Upload, 'Upload an API', 'Import your own OpenAPI spec', null],
	];
	return (
		<div
			data-testid="landing-shortcuts"
			className="relative z-[1] mx-auto mt-3 grid max-w-[960px] gap-2 sm:grid-cols-3"
		>
			{shortcuts.map(([Icon, title, detail, href]) => {
				const body = (
					<>
						<span className="bg-surface-tonal text-foreground-sub group-hover:bg-primary/15 group-hover:text-primary group-focus-visible:bg-primary/15 group-focus-visible:text-primary grid size-7 shrink-0 place-items-center rounded-[9px] transition-colors duration-[160ms]">
							<Icon className="h-3.5 w-3.5" />
						</span>
						<span className="min-w-0 flex-1">
							<span className="text-foreground block text-sm font-semibold">
								{title}
							</span>
							<span className="text-muted-foreground block text-xs">{detail}</span>
						</span>
						<ArrowRight
							aria-hidden="true"
							className="text-foreground-faint group-hover:text-primary group-focus-visible:text-primary h-4 w-4 shrink-0 transition-[transform,color] duration-[160ms] ease-(--ease-out-soft) group-hover:translate-x-1 group-focus-visible:translate-x-1 motion-reduce:transition-colors motion-reduce:group-hover:translate-x-0 motion-reduce:group-focus-visible:translate-x-0"
						/>
					</>
				);
				return href ? (
					<AppLink key={title} href={href} className={TILE}>
						{body}
					</AppLink>
				) : (
					<Button
						key={title}
						variant="ghost"
						onClick={() => setUploadOpen(true)}
						className={cn(
							TILE,
							'hover:text-foreground h-auto w-full justify-start font-normal',
						)}
					>
						{body}
					</Button>
				);
			})}
			<ImportSpecDialog
				open={uploadOpen}
				onClose={() => setUploadOpen(false)}
				onImported={([api, ...more]) =>
					void navigate(
						api && !more.length ? ROUTE_PATHS.workspaceApiHub(api) : ROUTES.library,
					)
				}
			/>
		</div>
	);
}
