/**
 * Act 1's supporting pieces:
 *
 *   TaskComposer    the opening beat: the task typed to the agent, like a chat
 *                   input. It morphs (shared layoutId) into the task bar.
 *   TaskBar         the job, pinned at the top of the act, with the plan as a
 *                   stepper: all four steps shown from the start as "not
 *                   started", each lighting up as the story reaches it, plus
 *                   the compact transport (pause/play, restart).
 *   ComparisonStrip the without-vs-with numbers, one compact divided card.
 */
import { useEffect, useState, type ComponentType } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
	AlertTriangle,
	ArrowUp,
	Check,
	KeyRound,
	LineChart,
	MessageSquareText,
	ShieldCheck,
	Trash2,
	UserCheck,
	X,
} from 'lucide-react';
import { AgentMark, Button, Card, VendorMark } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import type { PlanTone, WhyState } from '@/modules/agents/components/landing/act1/model';
import {
	COMPOSER_HOLD_MS,
	COMPOSER_MS_PER_CHAR,
	PLAN_SPANS,
	WHY_TASK,
} from '@/modules/agents/components/landing/act1/script';
import {
	DEMO_AGENT,
	ENV_SECRETS,
	type DemoApiId,
	type StoryApiId,
} from '@/modules/agents/components/landing/data/demoFixtures';
import {
	BeatSpanProgress,
	LandingControls,
} from '@/modules/agents/components/landing/LandingControls';
import { EASE_OUT_SOFT } from '@/modules/agents/components/landing/motion';
import type { LandingTimeline } from '@/modules/agents/components/landing/useLandingTimeline';
import { useTyped } from '@/modules/agents/components/landing/useTyped';

const eyebrow = 'text-muted-foreground text-[11px] tracking-wider uppercase';
const TASK_LAYOUT_ID = 'agents-landing-why-task';

/** The kit button, animatable: the composer's send button presses on its own. */
const MotionButton = motion.create(Button);

const PLAN_LABEL: Record<StoryApiId, string> = {
	github: 'Read GitHub issues',
	gmail: 'Email the summary',
	slack: 'Post to Slack',
	googledrive: 'Clean up Drive',
};

const PLAN_TONE_WORD: Record<PlanTone, string> = {
	todo: 'not started',
	active: 'running',
	ok: 'done',
	warn: 'waiting for you',
	fail: 'denied',
};

/* ------------------------------------------------------------ composer */

export function TaskComposer({
	reduced,
	onSend,
}: {
	reduced: boolean;
	/** Send now: skip the rest of the typing and the hold. */
	onSend?: () => void;
}) {
	const typed = useTyped(WHY_TASK, true, reduced, COMPOSER_MS_PER_CHAR);
	const done = typed.length === WHY_TASK.length;
	// Fully typed → read it (caret blinking) → the send button presses → it sends.
	const [pressed, setPressed] = useState(false);
	useEffect(() => {
		if (!done || reduced) return undefined;
		const t = window.setTimeout(() => setPressed(true), COMPOSER_HOLD_MS - 300);
		return () => window.clearTimeout(t);
	}, [done, reduced]);
	return (
		<motion.div
			layoutId={TASK_LAYOUT_ID}
			className="border-border bg-card mx-auto w-full max-w-2xl rounded-xl border p-4 shadow-sm"
			data-testid="task-composer"
			transition={{ type: 'spring', stiffness: 260, damping: 32 }}
		>
			<p className="text-muted-foreground mb-2 flex items-center gap-1.5 text-[11px]">
				You <span aria-hidden="true">→</span>
				<span className="text-foreground font-mono">{DEMO_AGENT.name}</span>
			</p>
			<div className="flex items-start gap-3">
				<AgentMark size="sm" />
				{/* Typed for the eye; the sr-only copy below reads it whole, once. */}
				<p
					aria-hidden="true"
					className="text-foreground min-h-10 flex-1 text-sm leading-relaxed"
				>
					{typed}
					{!pressed && (
						<span
							aria-hidden="true"
							className={cn(
								'bg-primary ml-0.5 inline-block h-3.5 align-[-2px]',
								done ? 'animate-caret w-0.5' : 'w-1.5 animate-pulse',
							)}
						/>
					)}
				</p>
				<MotionButton
					variant="ghost"
					size="icon"
					onClick={onSend}
					aria-label={`Send the task to ${DEMO_AGENT.name}`}
					data-testid="composer-send"
					data-state={pressed ? 'pressed' : done ? 'ready' : 'typing'}
					className={cn(
						'mt-1 h-7 w-7 shrink-0 rounded-full p-0 transition-colors duration-300 focus-visible:ring-offset-0 active:scale-100',
						done
							? 'bg-primary text-primary-foreground hover:bg-primary hover:text-primary-foreground'
							: 'bg-muted text-muted-foreground hover:bg-muted hover:text-muted-foreground',
						pressed && 'ring-primary/40 ring-4',
					)}
					animate={pressed ? { scale: [1, 0.86, 1] } : { scale: 1 }}
					transition={{ duration: 0.35, ease: EASE_OUT_SOFT }}
				>
					<ArrowUp className="h-4 w-4" aria-hidden="true" />
				</MotionButton>
			</div>
			<p className="sr-only">{WHY_TASK}</p>
		</motion.div>
	);
}

/* ------------------------------------------------------------- stepper */

/** Each tone's look; hover keeps it (the pill is a seek target, not an action). */
const STEP_CLASS: Record<PlanTone, string> = {
	todo: 'border-border border-dashed text-muted-foreground hover:bg-transparent hover:text-muted-foreground',
	active: 'border-primary/60 bg-primary/10 text-foreground hover:bg-primary/10',
	ok: 'border-success/40 text-foreground hover:bg-transparent',
	warn: 'border-warning/60 bg-warning/10 text-foreground hover:bg-warning/10',
	fail: 'border-danger/50 bg-danger/10 text-foreground hover:bg-danger/10',
};

function StepGlyph({ tone, n }: { tone: PlanTone; n: number }) {
	const base =
		'inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full font-mono text-[9px] font-bold';
	if (tone === 'ok')
		return (
			<span className={cn(base, 'bg-success text-background')}>
				<Check className="h-2.5 w-2.5" aria-hidden="true" />
			</span>
		);
	if (tone === 'fail')
		return (
			<span className={cn(base, 'bg-danger text-background')}>
				<X className="h-2.5 w-2.5" aria-hidden="true" />
			</span>
		);
	if (tone === 'warn')
		return <AlertTriangle className="text-warning h-3.5 w-3.5" aria-hidden="true" />;
	if (tone === 'todo')
		return <span className={cn(base, 'border-border text-muted-foreground border')}>{n}</span>;
	return <span className={cn(base, 'bg-primary text-primary-foreground')}>{n}</span>;
}

export function TaskBar({
	timeline,
	hoverApi,
}: {
	timeline: LandingTimeline<WhyState>;
	/** An Activity row under the pointer: its step lights up. */
	hoverApi?: DemoApiId | null;
}) {
	const { state, reduced } = timeline;
	return (
		<motion.div
			layoutId={TASK_LAYOUT_ID}
			transition={{ type: 'spring', stiffness: 260, damping: 32 }}
			className="border-border bg-card rounded-xl border px-3.5 py-2.5"
			data-testid="task-bar"
		>
			<div className="flex flex-col gap-2.5 xl:flex-row xl:items-center xl:gap-4">
				<div className="flex min-w-0 items-center gap-2.5 xl:max-w-[26rem]">
					<AgentMark size="sm" />
					<div className="min-w-0">
						<p className={eyebrow}>Your task for {DEMO_AGENT.name}</p>
						<p className="text-foreground truncate text-[13px]" title={WHY_TASK}>
							{WHY_TASK}
						</p>
					</div>
				</div>
				<div className="flex min-w-0 flex-1 items-center gap-2">
					<ol
						className="flex min-w-0 flex-1 flex-wrap items-center gap-y-1.5"
						aria-label="The plan"
					>
						{PLAN_SPANS.map(({ api, first, last }, i) => {
							const tone = state.plan[api];
							const started = tone !== 'todo';
							return (
								<li
									key={api}
									className="flex items-center"
									data-plan={api}
									data-tone={tone}
								>
									{i > 0 && (
										<span
											aria-hidden="true"
											data-connector={started ? 'filled' : 'empty'}
											className="bg-border relative mx-1.5 h-px w-5 overflow-hidden"
										>
											<motion.span
												className="bg-primary/70 absolute inset-0 origin-left"
												initial={false}
												animate={{ scaleX: started ? 1 : 0 }}
												transition={{
													duration: reduced ? 0 : 0.7,
													ease: EASE_OUT_SOFT,
												}}
											/>
										</span>
									)}
									<Button
										variant="ghost"
										size="sm"
										onClick={() => timeline.seek(first)}
										aria-current={
											tone === 'active' || tone === 'warn'
												? 'step'
												: undefined
										}
										className={cn(
											'relative gap-1.5 overflow-hidden rounded-full border px-2.5 py-1 text-xs font-normal transition-colors duration-500 focus-visible:ring-offset-0 active:scale-100',
											STEP_CLASS[tone],
											hoverApi === api && 'ring-primary/60 ring-2',
										)}
									>
										<StepGlyph tone={tone} n={i + 1} />
										<span
											className={cn(
												'inline-flex transition-[filter,opacity] duration-500',
												!started && 'opacity-60 grayscale',
											)}
										>
											<VendorMark slug={api} size="xs" />
										</span>
										<span>{PLAN_LABEL[api]}</span>
										<span className="sr-only">({PLAN_TONE_WORD[tone]})</span>
										{(tone === 'active' || tone === 'warn') && (
											<BeatSpanProgress
												timeline={timeline}
												first={first}
												length={last - first + 1}
												className="bg-primary/70 inset-x-2 bottom-0.5 h-0.5 rounded-full"
											/>
										)}
									</Button>
								</li>
							);
						})}
					</ol>
					<LandingControls timeline={timeline} noun="walkthrough" variant="compact" />
				</div>
			</div>
			<Narration caption={timeline.beat?.caption} reduced={reduced} />
		</motion.div>
	);
}

/**
 * The narration line, under the plan: what this beat means, in one sentence.
 * The act's one `aria-live` region (announced per beat). Captions crossfade
 * with a small slide; reduced motion swaps them.
 */
function Narration({ caption, reduced }: { caption?: string; reduced: boolean }) {
	return (
		<div className="border-border mt-2.5 flex items-start gap-2 border-t pt-2">
			<MessageSquareText
				className="text-primary mt-[3px] h-3.5 w-3.5 shrink-0"
				aria-hidden="true"
			/>
			<div className="relative min-h-5 min-w-0 flex-1">
				<p className="sr-only" aria-live="polite" data-testid="landing-caption">
					{caption}
				</p>
				<AnimatePresence initial={false} mode="popLayout">
					<motion.p
						key={caption ?? ''}
						aria-hidden="true"
						data-testid="landing-caption-text"
						className="text-foreground text-[13px] leading-5"
						initial={reduced ? false : { opacity: 0, y: 6 }}
						animate={{ opacity: 1, y: 0 }}
						exit={reduced ? undefined : { opacity: 0, y: -6 }}
						transition={{ duration: reduced ? 0 : 0.35, ease: EASE_OUT_SOFT }}
					>
						{caption}
					</motion.p>
				</AnimatePresence>
			</div>
		</div>
	);
}

/* ------------------------------------------------------ comparison strip */

interface Cell {
	key: string;
	label: string;
	icon: ComponentType<{ className?: string }>;
	with: string;
	without: string;
}

/** A value that rolls in when it changes, with a brief highlight. */
function RollingValue({
	value,
	reduced,
	className,
}: {
	value: string;
	reduced: boolean;
	className?: string;
}) {
	return (
		<span className="relative inline-flex overflow-hidden">
			<AnimatePresence mode="popLayout" initial={false}>
				<motion.span
					key={value}
					className={cn('inline-block rounded px-0.5', className)}
					initial={reduced ? false : { y: '70%', opacity: 0 }}
					animate={
						reduced
							? { y: 0, opacity: 1 }
							: {
									y: 0,
									opacity: 1,
									backgroundColor: [
										'hsl(var(--primary) / 0.22)',
										'hsl(var(--primary) / 0)',
									],
								}
					}
					exit={reduced ? undefined : { y: '-70%', opacity: 0 }}
					transition={{ duration: reduced ? 0 : 0.45, ease: EASE_OUT_SOFT }}
				>
					{value}
				</motion.span>
			</AnimatePresence>
		</span>
	);
}

export function ComparisonStrip({
	state,
	reduced = false,
}: {
	state: WhyState;
	reduced?: boolean;
}) {
	const cells: Cell[] = [
		{
			key: 'keys',
			label: 'Keys the agent holds',
			icon: KeyRound,
			with: '0',
			without: String(ENV_SECRETS.length),
		},
		{
			key: 'approvals',
			label: 'Approvals asked',
			icon: UserCheck,
			with: String(state.stats.approvals),
			without: '0',
		},
		{
			key: 'traced',
			label: 'Calls traced',
			icon: LineChart,
			with: String(state.stats.traced),
			without: '0',
		},
		{
			key: 'stopped',
			label: 'Bad calls stopped',
			icon: ShieldCheck,
			with: String(state.stats.stopped),
			without: '0',
		},
		{
			key: 'lost',
			label: 'Files lost',
			icon: Trash2,
			with: '0',
			without: String(state.withoutLost),
		},
	];
	return (
		<Card data-testid="comparison-strip" className="px-3.5 pt-2 pb-2.5">
			<h3 className="font-heading text-foreground mb-1.5 text-xs font-semibold">
				Jentic One vs no gateway
			</h3>
			<dl className="divide-border grid grid-cols-2 gap-y-2 sm:grid-cols-5 sm:divide-x">
				{cells.map((c) => {
					const IconCmp = c.icon;
					return (
						<div key={c.key} data-stat={c.key} className="min-w-0 px-3 first:pl-0">
							<dt className="text-muted-foreground flex items-center gap-1.5 text-[11px]">
								<IconCmp className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
								<span className="truncate">{c.label}</span>
							</dt>
							<dd className="mt-0.5 flex items-end gap-3">
								<span className="flex flex-col">
									<span className="text-success/80 text-[9.5px] leading-none">
										With Jentic One
									</span>
									<RollingValue
										value={c.with}
										reduced={reduced}
										className="text-success font-heading text-xl leading-tight font-semibold tabular-nums"
									/>
								</span>
								<span className="flex flex-col">
									<span className="text-muted-foreground text-[9.5px] leading-none">
										Without
									</span>
									<RollingValue
										value={c.without}
										reduced={reduced}
										className={cn(
											'font-mono text-sm leading-tight tabular-nums',
											c.without === '0'
												? 'text-muted-foreground'
												: 'text-danger font-semibold',
										)}
									/>
								</span>
							</dd>
						</div>
					);
				})}
			</dl>
		</Card>
	);
}
