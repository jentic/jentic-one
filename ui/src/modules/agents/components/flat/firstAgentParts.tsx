/**
 * The pieces of `FirstAgentLanding`'s primary card: the register command, the
 * three-step stepper, the live status line, and the arrived agent's details
 * with its next action (Approve / Deny, then its first API).
 */
import { useEffect, useId, useReducer, type ReactNode } from 'react';
import { AnimatePresence, motion, type Transition } from 'framer-motion';
import {
	Bot,
	Check,
	CircleCheck,
	Clock,
	Plus,
	Terminal,
	TriangleAlert,
	type LucideIcon,
} from 'lucide-react';
import {
	ActorLabel,
	ActorStatusBadge,
	AppLink,
	Badge,
	Button,
	CopyButton,
	ErrorAlert,
	Input,
	Label,
	Skeleton,
	VendorMark,
} from '@/shared/ui';
import { cn, formatTimestamp, timeAgo } from '@/shared/lib/utils';
import { ROUTES } from '@/shared/app/routes';
import {
	ACTION_LABEL,
	ACTION_VARIANT,
	useAgentApiKeyInfo,
	useAgentScopes,
	type AgentEntity,
} from '@/modules/agents/api';
import { EASE_OUT_SOFT } from '@/modules/agents/components/flat/GhostFleet';
import { AGENT_NAME_MAX_LENGTH, agentNameError } from '@/modules/agents/lib/agentName';
import type { FirstAgentExit, FirstAgentPhase } from '@/modules/agents/lib/firstRun';
import { useGithubPick } from '@/modules/agents/lib/githubPick';
import { useRegisterTarget } from '@/modules/agents/lib/useRegisterTarget';
import { scopeRisk, type ScopeRisk } from '@/modules/agents/lib/requestedScopes';
import {
	DEFAULT_REGISTER_NAME,
	commandText,
	registerCommandTokens,
	type CommandTone,
} from '@/modules/agents/lib/registerCommand';

/** Card header: glyph, title, one line, trailing badge. The title takes focus
 * (programmatically only) when the card's content changes under the keyboard. */
function CardHeader({
	titleId,
	glyph,
	title,
	detail,
	badge,
}: {
	titleId: string;
	glyph: ReactNode;
	title: ReactNode;
	detail: ReactNode;
	badge: ReactNode;
}) {
	return (
		<div className="flex items-start gap-3">
			{glyph}
			<div className="min-w-0 flex-1">
				<h2
					id={titleId}
					tabIndex={-1}
					className="font-heading text-foreground text-[17px] font-semibold tracking-[-0.01em] [overflow-wrap:anywhere] outline-none"
				>
					{title}
				</h2>
				<p className="text-muted-foreground mt-[3px] text-sm leading-normal">{detail}</p>
			</div>
			<span className="shrink-0">{badge}</span>
		</div>
	);
}

// ---------------------------------------------------------------------------
// Listening: the register command
// ---------------------------------------------------------------------------

/** How each word of the displayed command is coloured. */
const TONE_CLASS: Record<CommandTone, string | undefined> = {
	program: 'text-accent-yellow',
	plain: undefined,
	flag: 'text-muted-foreground/70',
	url: 'text-accent-blue',
	value: 'text-success',
	placeholder: 'text-muted-foreground',
};

export function RegisterCommand({
	titleId,
	name,
	onNameChange,
}: {
	titleId: string;
	name: string;
	onNameChange: (name: string) => void;
}) {
	const inputId = useId();
	const nameError = agentNameError(name);
	const target = useRegisterTarget();
	const tokens = registerCommandTokens({
		...target,
		name: nameError ? DEFAULT_REGISTER_NAME : name.trim(),
	});

	return (
		<div>
			<CardHeader
				titleId={titleId}
				glyph={
					<span
						aria-hidden="true"
						className="text-primary bg-primary/10 ring-primary/25 grid h-9 w-9 shrink-0 place-items-center rounded-[10px] ring-1 ring-inset"
					>
						<Terminal className="h-4 w-4" />
					</span>
				}
				title="Let your agent register itself"
				detail="Run one command where your agent runs. It signs up with its own key and shows up here for you to approve."
				badge={<Badge className="font-sans text-[11px] font-semibold">Recommended</Badge>}
			/>

			<div className="mt-4 mb-2.5 flex items-start gap-3">
				<Label
					htmlFor={inputId}
					className="text-foreground/90 mt-2 text-[13px] font-semibold whitespace-nowrap"
				>
					Agent name
				</Label>
				<div className="w-full max-w-[260px]">
					<Input
						id={inputId}
						size="sm"
						value={name}
						onChange={(e) => onNameChange(e.target.value)}
						error={nameError ?? undefined}
						maxLength={AGENT_NAME_MAX_LENGTH}
						spellCheck={false}
						autoComplete="off"
						className="h-[34px] font-mono text-[13px]"
					/>
				</div>
			</div>

			<div className="border-border bg-background overflow-hidden rounded-[10px] border">
				<div className="border-border/60 bg-card/70 flex h-[34px] items-center gap-2 border-b pr-2 pl-3">
					<span aria-hidden="true" className="flex gap-1.5">
						{[0, 1, 2].map((i) => (
							<span key={i} className="bg-border h-[9px] w-[9px] rounded-full" />
						))}
					</span>
					<span className="text-muted-foreground/70 flex-1 text-center font-mono text-[11px]">
						where your agent runs
					</span>
					<CopyButton
						value={commandText(tokens)}
						label="Copy"
						ariaLabel="Copy the register command"
						toastMessage="Command copied"
						variant="ghost"
						className="text-muted-foreground hover:text-foreground h-6 gap-1.5 px-2 text-xs font-semibold [&_svg]:h-3.5 [&_svg]:w-3.5"
					/>
				</div>
				<pre
					data-testid="register-command"
					className="text-foreground/90 px-3.5 py-3 font-mono text-[13px] leading-relaxed [overflow-wrap:anywhere] whitespace-pre-wrap"
				>
					<span className="text-success select-none">$</span>
					{tokens.map((token, i) => (
						<span key={i}>
							{' '}
							<span className={TONE_CLASS[token.tone]}>{token.text}</span>
						</span>
					))}
				</pre>
			</div>
			{target.backend === 'remote' && (
				<p
					data-testid="register-broker-note"
					className="text-muted-foreground mt-2 text-xs"
				>
					On a remote install <code className="font-mono">--broker-url</code> is required
					— without it <code className="font-mono">jentic execute</code> fail-closes.{' '}
					{target.brokerUrl
						? "The command carries this instance's broker (data plane) URL."
						: 'Ask whoever deployed this instance for the broker (data plane) URL and put it in place of <broker-url>.'}
				</p>
			)}
			<CliInstallHint />
		</div>
	);
}

/** The install one-liners, as `cli/README.md` gives them. */
const CLI_INSTALL = {
	brew: 'brew install --cask jentic/tap/jentic',
	script: 'curl -fsSL https://raw.githubusercontent.com/jentic/jentic-one/main/tools/install.sh | sh',
} as const;

/** How to get `jentic` for an operator who has never installed it. */
function CliInstallHint() {
	return (
		<p
			data-testid="cli-install-hint"
			className="text-muted-foreground mt-2 text-xs leading-relaxed [overflow-wrap:anywhere]"
		>
			Don&apos;t have the CLI?{' '}
			<code className="text-foreground/90 font-mono">{CLI_INSTALL.brew}</code> or{' '}
			<code className="text-foreground/90 font-mono">{CLI_INSTALL.script}</code>. Setting up a
			local coding agent? Run{' '}
			<code className="text-foreground/90 font-mono">jentic setup</code> instead — it
			registers too, and adds an isolated account and the agent skills.{' '}
			<AppLink href={`${ROUTES.docs}#installation`} data-no-transition className="underline">
				Installation docs
			</AppLink>
		</p>
	);
}

// ---------------------------------------------------------------------------
// The stepper
// ---------------------------------------------------------------------------

const STEPS: Array<{ icon: LucideIcon; title: string; detail: string }> = [
	{ icon: Terminal, title: 'Run the command', detail: 'The CLI makes its keypair and signs up' },
	{
		icon: Clock,
		title: 'It appears here as pending',
		detail: 'In the tab below, with no access',
	},
	{ icon: CircleCheck, title: 'You approve it', detail: 'Then add the APIs it can use' },
];

type StepState = 'done' | 'current' | 'upcoming';

const STEP_STATES: Record<FirstAgentPhase, StepState[]> = {
	listening: ['current', 'upcoming', 'upcoming'],
	arrived: ['done', 'done', 'current'],
	approved: ['done', 'done', 'done'],
};

/** How full each connector is (1→2, 2→3): arrival leads the line on toward
 * step 3, which approval completes. */
const SEGMENT_FILL: Record<FirstAgentPhase, [number, number]> = {
	listening: [0, 0],
	arrived: [1, 0.5],
	approved: [1, 1],
};

/** One full connector's fill time. */
const SEGMENT_S = 0.6;

export function Stepper({
	phase,
	reducedMotion,
}: {
	phase: FirstAgentPhase;
	reducedMotion: boolean;
}) {
	const states = STEP_STATES[phase];
	const fills = SEGMENT_FILL[phase];
	return (
		<ol
			aria-label="Registration progress"
			data-testid="register-stepper"
			className="mt-4 grid grid-cols-3 gap-3"
		>
			{STEPS.map(({ icon: Icon, title, detail }, i) => {
				const state = states[i];
				const fill = i > 0 ? fills[i - 1] : 0;
				// The second connector waits for the first, so the line reads as one run.
				const delay = !reducedMotion && i === 2 && phase === 'arrived' ? SEGMENT_S : 0;
				return (
					<li
						key={title}
						data-state={state}
						aria-current={state === 'current' ? 'step' : undefined}
						className="relative flex flex-col items-start gap-2"
					>
						{i > 0 && (
							// The connector from the previous step's icon to this one.
							<span
								aria-hidden="true"
								className="bg-border absolute top-3.5 right-[calc(100%+6px)] h-px w-[calc(100%-28px)] overflow-hidden"
							>
								<motion.span
									className="bg-success absolute inset-0 origin-left"
									initial={false}
									animate={{ scaleX: fill }}
									transition={
										reducedMotion
											? { duration: 0 }
											: { duration: SEGMENT_S, ease: EASE_OUT_SOFT, delay }
									}
								/>
							</span>
						)}
						<span
							aria-hidden="true"
							className={cn(
								'bg-card relative z-[1] grid h-7 w-7 shrink-0 place-items-center rounded-full transition-[color,box-shadow,background-color] duration-500 ease-(--ease-out-soft) ring-inset',
								state === 'done' &&
									'text-success bg-success/10 ring-success/45 ring-1',
								state === 'current' &&
									'text-primary ring-primary shadow-[0_0_0_4px_hsl(var(--primary)/0.14)] ring-2',
								state === 'upcoming' && 'text-muted-foreground ring-border ring-1',
							)}
						>
							{state === 'done' ? (
								<Check className="h-3.5 w-3.5" />
							) : (
								<Icon className="h-3.5 w-3.5" />
							)}
						</span>
						<span>
							<span
								className={cn(
									'block text-[13px] font-semibold transition-colors duration-500',
									state === 'done' ? 'text-success' : 'text-foreground',
								)}
							>
								{title}
								{state === 'done' && <span className="sr-only"> (done)</span>}
							</span>
							<span className="text-muted-foreground mt-0.5 block text-xs leading-snug">
								{detail}
							</span>
						</span>
					</li>
				);
			})}
		</ol>
	);
}

// ---------------------------------------------------------------------------
// The live status line
// ---------------------------------------------------------------------------

/** The card's one live line: what the landing is waiting on now. */
export function StatusLine({ phase, name }: { phase: FirstAgentPhase; name: string | null }) {
	return (
		<div className="border-border/60 mt-3.5 border-t pt-3">
			<p
				role="status"
				aria-live="polite"
				data-testid="register-status"
				className={cn(
					'flex items-center gap-2 text-xs transition-colors duration-300',
					phase === 'listening' ? 'text-muted-foreground' : 'text-foreground/90',
				)}
			>
				<span
					aria-hidden="true"
					className={cn(
						'h-[7px] w-[7px] shrink-0 rounded-full transition-colors duration-300',
						phase === 'listening' && 'bg-primary animate-soft-pulse',
						phase === 'arrived' && 'bg-accent-orange',
						phase === 'approved' && 'bg-success',
					)}
				/>
				{phase === 'listening' || name == null ? (
					<span>Listening for new agents…</span>
				) : phase === 'arrived' ? (
					<span>
						<b className="text-foreground font-mono font-medium">{name}</b> just
						registered · awaiting your approval
					</span>
				) : (
					<span>
						<b className="text-foreground font-mono font-medium">{name}</b> is approved
						· it can authenticate now
					</span>
				)}
			</p>
		</div>
	);
}

// ---------------------------------------------------------------------------
// The arrived agent: details, then its next action
// ---------------------------------------------------------------------------

/** How often "Registered 12s ago" re-reads the clock. */
const RELATIVE_TICK_MS = 15_000;

function relativeTime(iso: string): string {
	const ago = timeAgo(iso);
	return ago === 'now' ? 'just now' : `${ago} ago`;
}

export function AgentDetails({
	titleId,
	agent,
	phase,
	onApprove,
	approvePending,
	onDeny,
	onExit,
	fade,
	expectedName,
	morePending,
	onShowFleet,
}: {
	titleId: string;
	agent: AgentEntity;
	phase: Exclude<FirstAgentPhase, 'listening'>;
	onApprove: () => void;
	approvePending: boolean;
	onDeny: () => void;
	onExit: (exit: FirstAgentExit) => void;
	fade: Transition;
	/** The name the command on this page registers with, or null when this
	 * session never showed it. */
	expectedName: string | null;
	/** Other agents waiting besides this one — pointed at, never hidden. */
	morePending: number;
	onShowFleet: () => void;
}) {
	// "Registered 12s ago" matters while the agent waits; once approved it stops.
	const [, tick] = useReducer((n: number) => n + 1, 0);
	useEffect(() => {
		if (phase === 'approved') return;
		const id = window.setInterval(tick, RELATIVE_TICK_MS);
		return () => window.clearInterval(id);
	}, [phase]);
	const selfRegistered = agent.attribution.registeredBy === 'self';
	// Approval makes the requested scopes live, so Approve waits until they are
	// read and on screen.
	const scopes = useAgentScopes(agent.id);
	const scopesUnread = scopes.isPending || scopes.isError;

	return (
		<div data-testid="arrival-card">
			<CardHeader
				titleId={titleId}
				glyph={
					// Neutral on purpose: anyone who can reach `/register` can arrive
					// here under any name, so the card lends it no brand.
					<span
						aria-hidden="true"
						className="text-muted-foreground bg-muted/60 ring-border grid h-9 w-9 shrink-0 place-items-center rounded-[10px] ring-1 ring-inset"
					>
						<Bot className="h-4 w-4" />
					</span>
				}
				title={<span className="font-mono">{agent.name}</span>}
				detail={
					// When it registered, in full: the time is how an operator tells
					// their own run from someone else's.
					<span data-testid="arrival-registered">
						Registered {relativeTime(agent.createdAt)} ·{' '}
						<time dateTime={agent.createdAt} className="text-foreground/90 font-medium">
							{formatTimestamp(agent.createdAt)}
						</time>
					</span>
				}
				badge={<ActorStatusBadge status={agent.status} />}
			/>
			<AgentFacts agent={agent} selfRegistered={selfRegistered} scopes={scopes} />

			<AnimatePresence mode="wait" initial={false}>
				<motion.div
					key={phase}
					initial={{ opacity: 0, y: 6 }}
					animate={{ opacity: 1, y: 0 }}
					exit={{ opacity: 0, y: -4 }}
					transition={fade}
				>
					{phase === 'arrived' ? (
						<div className="mt-4">
							<ArrivalWarnings
								agentName={agent.name}
								expectedName={expectedName}
								morePending={morePending}
							/>
							<p className="text-foreground/90 text-sm">
								It has its own key but can&apos;t make calls until you approve it.
							</p>
							<div className="mt-3 flex flex-wrap items-center gap-2">
								<Button
									variant={ACTION_VARIANT.approve}
									loading={approvePending}
									disabled={scopesUnread}
									onClick={onApprove}
									aria-label={`${ACTION_LABEL.approve} ${agent.name}`}
								>
									<CircleCheck className="h-4 w-4" />
									{ACTION_LABEL.approve}
								</Button>
								<Button
									variant={ACTION_VARIANT.deny}
									disabled={approvePending}
									onClick={onDeny}
									aria-label={`${ACTION_LABEL.deny} ${agent.name}`}
								>
									{ACTION_LABEL.deny}
								</Button>
							</div>
							{scopesUnread && (
								<p
									data-testid="approve-waits-for-scopes"
									className="text-muted-foreground mt-2 text-xs"
								>
									Approve is available once the scopes it requests are read.
								</p>
							)}
						</div>
					) : (
						<FirstApiPanel agent={agent} onExit={onExit} />
					)}
				</motion.div>
			</AnimatePresence>
			{morePending > 0 && (
				<Button
					variant="ghost"
					size="sm"
					onClick={onShowFleet}
					data-testid="more-pending"
					className="text-muted-foreground hover:text-foreground mt-3 h-auto px-1 py-0.5 text-xs underline decoration-dotted underline-offset-2"
				>
					+{morePending} more waiting for approval
				</Button>
			)}
		</div>
	);
}

/**
 * What to check before approving. Registration is open to anyone who can reach
 * the instance and the name is free text, so the card says when the arrival
 * may not be the operator's own: other agents are waiting too, or its name is
 * not the one the command on this page registers with.
 */
function ArrivalWarnings({
	agentName,
	expectedName,
	morePending,
}: {
	agentName: string;
	expectedName: string | null;
	morePending: number;
}) {
	const nameDiffers = expectedName != null && expectedName !== agentName;
	if (!nameDiffers && morePending === 0) return null;
	return (
		<div
			data-testid="arrival-warnings"
			className="border-warning/40 bg-warning/5 mb-3 space-y-1.5 rounded-lg border px-3 py-2.5"
		>
			{nameDiffers && (
				<p
					data-testid="arrival-name-warning"
					className="text-foreground flex items-start gap-2 text-xs"
				>
					<TriangleAlert className="text-warning mt-0.5 h-3.5 w-3.5 shrink-0" />
					<span>
						It registered as <b className="font-mono font-medium">{agentName}</b>, not{' '}
						<b className="font-mono font-medium">{expectedName}</b> — the name in your
						command. Make sure it is yours before approving.
					</span>
				</p>
			)}
			{morePending > 0 && (
				<p
					data-testid="arrival-others-warning"
					className="text-foreground flex items-start gap-2 text-xs"
				>
					<TriangleAlert className="text-warning mt-0.5 h-3.5 w-3.5 shrink-0" />
					<span>
						Other agents are also waiting — check the name and time before approving.
					</span>
				</p>
			)}
		</div>
	);
}

/** One fact about the agent. */
function Fact({ label, children }: { label: string; children: ReactNode }) {
	return (
		<div className="min-w-0">
			<dt className="text-muted-foreground/80 text-[10px] font-medium tracking-wider uppercase">
				{label}
			</dt>
			<dd className="text-foreground/90 mt-0.5 truncate text-xs">{children}</dd>
		</div>
	);
}

/**
 * What the API says about where the agent came from — only the facts it has.
 * A self-registration carries no owner, no API key and usually no scopes, so
 * most rows only show for an agent someone set up.
 */
function AgentFacts({
	agent,
	selfRegistered,
	scopes,
}: {
	agent: AgentEntity;
	selfRegistered: boolean;
	scopes: ReturnType<typeof useAgentScopes>;
}) {
	const keyInfo = useAgentApiKeyInfo(agent.hasApiKey ? agent.id : null);

	return (
		<dl
			data-testid="agent-facts"
			className="border-border/60 bg-background/35 mt-4 grid grid-cols-2 gap-x-4 gap-y-3 rounded-lg border px-4 py-3 sm:grid-cols-3"
		>
			<Fact label="How">
				{selfRegistered ? (
					'Self-registered from the CLI'
				) : agent.attribution.registeredBy ? (
					<>
						Registered by <ActorLabel actorId={agent.attribution.registeredBy} />
					</>
				) : (
					'Created here'
				)}
			</Fact>
			<Fact label="Signs in with">
				{agent.hasApiKey
					? 'An API key'
					: selfRegistered
						? 'Its own keypair'
						: 'Nothing yet'}
			</Fact>
			<Fact label="Agent ID">
				<span className="font-mono">{agent.id}</span>
			</Fact>
			{keyInfo.data && (
				<Fact label="Key ID">
					<span className="font-mono">{keyInfo.data.id}</span>
				</Fact>
			)}
			{agent.ownerId && (
				<Fact label="Owner">
					<ActorLabel actorId={agent.ownerId} />
				</Fact>
			)}
			{agent.parentAgentId && (
				<Fact label="Parent agent">
					<ActorLabel actorId={agent.parentAgentId} />
				</Fact>
			)}
			{agent.description && <Fact label="Description">{agent.description}</Fact>}
			<RequestedScopes scopes={scopes} />
		</dl>
	);
}

const RISK_VARIANT: Record<ScopeRisk, 'danger' | 'warning'> = {
	admin: 'danger',
	write: 'warning',
};

const RISK_LABEL: Record<ScopeRisk, string> = {
	admin: 'administers the organisation',
	write: 'can change data',
};

/**
 * Every scope the agent asked for, in full: approval grants all of them at
 * once, so none may hide behind an ellipsis. Scopes that change data or
 * administer the organisation are flagged; an unread list says so rather than
 * reading as "no scopes".
 */
function RequestedScopes({ scopes }: { scopes: ReturnType<typeof useAgentScopes> }) {
	const list = scopes.data ?? [];
	const risky = list.filter((scope) => scopeRisk(scope) != null).length;
	return (
		<div data-testid="requested-scopes" className="col-span-full min-w-0">
			<dt className="text-muted-foreground/80 text-[10px] font-medium tracking-wider uppercase">
				Scopes
			</dt>
			<dd className="text-foreground/90 mt-1 text-xs">
				{scopes.isPending ? (
					<span aria-busy="true" className="flex flex-wrap gap-1.5">
						<span className="sr-only">Reading the scopes it requests…</span>
						<Skeleton className="h-5 w-24 rounded-full" />
						<Skeleton className="h-5 w-32 rounded-full" />
					</span>
				) : scopes.isError ? (
					<ErrorAlert
						message="Could not read the scopes this agent requests."
						onRetry={() => void scopes.refetch()}
						retrying={scopes.isFetching}
					/>
				) : list.length === 0 ? (
					'Requests no scopes'
				) : (
					<>
						<ul aria-label="Requested scopes" className="flex flex-wrap gap-1.5">
							{list.map((scope) => {
								const risk = scopeRisk(scope);
								return (
									<li key={scope} className="max-w-full">
										<Badge
											variant={risk ? RISK_VARIANT[risk] : 'default'}
											data-risk={risk ?? undefined}
											className="max-w-full [overflow-wrap:anywhere]"
										>
											{risk && (
												<TriangleAlert
													className="h-3 w-3 shrink-0"
													aria-hidden="true"
												/>
											)}
											{scope}
											{risk && (
												<span className="sr-only">
													{' '}
													({RISK_LABEL[risk]})
												</span>
											)}
										</Badge>
									</li>
								);
							})}
						</ul>
						{risky > 0 && (
							<p className="text-muted-foreground mt-1.5">
								{risky === 1
									? '1 of these can change data or administer your organisation.'
									: `${risky} of these can change data or administer your organisation.`}{' '}
								Approving grants every scope listed.
							</p>
						)}
					</>
				)}
			</dd>
		</div>
	);
}

/** After approval: its first API — GitHub when there is one to offer. Until
 * the workspace and the catalog have answered, a stable placeholder stands in,
 * so the heading never flips from one offer to the other. */
function FirstApiPanel({
	agent,
	onExit,
}: {
	agent: AgentEntity;
	onExit: (exit: FirstAgentExit) => void;
}) {
	const headingId = useId();
	const github = useGithubPick();
	const offerGithub = !github.loading && github.pick != null;
	const skip = (
		<Button
			variant="ghost"
			onClick={() => onExit({ kind: 'skip' })}
			className="text-muted-foreground hover:text-foreground"
		>
			Skip for now
		</Button>
	);

	return (
		<section
			aria-labelledby={headingId}
			aria-busy={github.loading || undefined}
			data-testid="first-api-panel"
			className="border-border/70 bg-background/40 mt-4 flex flex-wrap items-center gap-x-4 gap-y-3 rounded-[10px] border p-4"
		>
			<span
				aria-hidden="true"
				className="ring-border/60 grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-white ring-1"
			>
				{github.loading ? null : offerGithub ? (
					<VendorMark slug="github" size="md" />
				) : (
					<Plus className="h-4 w-4 text-neutral-700" />
				)}
			</span>
			<div className="min-w-[220px] flex-1">
				<h3 id={headingId} className="text-foreground text-sm font-semibold">
					{github.loading ? (
						<span className="sr-only">Finding a first API for {agent.name}…</span>
					) : offerGithub ? (
						<>
							Add GitHub to <span className="font-mono">{agent.name}</span>
						</>
					) : (
						<>
							Give <span className="font-mono">{agent.name}</span> its first API
						</>
					)}
				</h3>
				{github.loading ? (
					<>
						<Skeleton className="my-0.5 h-4 w-48" />
						<Skeleton className="mt-1.5 h-3 w-72 max-w-full" />
					</>
				) : (
					<p className="text-muted-foreground mt-0.5 text-xs">
						{offerGithub
							? 'Let it read issues and repos — you choose exactly what it can do.'
							: github.catalogUnavailable
								? "The public API catalog isn't available on this instance yet. Pick one of your workspace's APIs, or upload an OpenAPI spec."
								: 'Pick an API it can call, or upload an OpenAPI spec — you choose exactly what it can do.'}
					</p>
				)}
			</div>
			<div className="flex flex-wrap items-center gap-2">
				{github.loading ? null : offerGithub && github.pick ? (
					<>
						<Button
							onClick={() => {
								if (github.pick) onExit({ kind: 'queue', apis: [github.pick] });
							}}
						>
							Continue with GitHub
						</Button>
						<Button variant="secondary" onClick={() => onExit({ kind: 'tray' })}>
							Add another API
						</Button>
					</>
				) : (
					<Button onClick={() => onExit({ kind: 'tray' })}>
						<Plus className="h-4 w-4" />
						Add an API
					</Button>
				)}
				{skip}
			</div>
		</section>
	);
}
