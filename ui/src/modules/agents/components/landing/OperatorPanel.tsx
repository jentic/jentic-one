/**
 * OperatorPanel — what a person sees while the story plays, mirroring the two
 * real surfaces: the Notifications bell (every approval lands there) and the
 * Activity log (one list; the source filter is the Monitor's own All / Calls /
 * Jobs / Audit). The filter is live — the viewer can narrow the log mid-story.
 */
import { useState, type ReactNode } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Bell, Bot, MousePointer2, Pause } from 'lucide-react';
import { Card, SegmentedToggle, StatusGlyph, Tag, VendorMark } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import {
	ACTIVITY_SOURCE_OPTIONS,
	type ActivityFilter,
	type ActivityRow,
	type BellItem,
	type OperatorAgentStatus,
	type OperatorPointer,
} from '@/modules/agents/components/landing/operator';
import type { MonitorLanding } from '@/modules/agents/components/landing/act1/MonitorFlight';
import { EASE_GLIDE, EASE_OUT_SOFT } from '@/modules/agents/components/landing/motion';

const MAX_ROWS = 6;

const AGENT_STATUS: Record<
	'none' | 'pending' | 'active' | 'disabled',
	{ word: string; cls: string }
> = {
	none: { word: 'Not registered', cls: 'text-muted-foreground' },
	pending: { word: 'Pending', cls: 'text-warning' },
	active: { word: 'Active', cls: 'text-success' },
	disabled: { word: 'Paused', cls: 'text-warning' },
};

/**
 * A scripted pointer that glides onto its target (~1s), rests on it (~0.8s),
 * then presses it. It lives inside the target, so it needs no measuring;
 * transform only.
 */
function Pointer({
	show,
	reduced,
	children,
}: {
	show: boolean;
	reduced: boolean;
	children: ReactNode;
}) {
	return (
		<span className="relative inline-flex">
			<motion.span
				className="inline-flex"
				animate={show && !reduced ? { scale: [1, 1, 0.9, 1] } : { scale: 1 }}
				transition={{ duration: 2.2, times: [0, 0.82, 0.88, 0.97] }}
			>
				{children}
			</motion.span>
			{show && (
				<motion.span
					aria-hidden="true"
					data-testid="scripted-pointer"
					className="text-foreground pointer-events-none absolute top-1/2 left-1/2 z-10"
					initial={reduced ? false : { x: 60, y: 70, opacity: 0 }}
					animate={{ x: 0, y: 0, opacity: 1 }}
					transition={reduced ? { duration: 0 } : { duration: 1, ease: EASE_GLIDE }}
				>
					<MousePointer2 className="fill-background h-5 w-5 drop-shadow" />
				</motion.span>
			)}
		</span>
	);
}

const CHIP_TONE: Record<MonitorLanding['kind'], string> = {
	recorded: 'border-primary/50 text-primary',
	needs: 'border-warning/60 text-warning',
	denied: 'border-danger/60 text-danger',
	paused: 'border-warning/60 text-warning',
};

/** Where a landing is, for one Monitor item: hidden in flight, then shown. */
function landingProps(landing: MonitorLanding | null | undefined, reduced: boolean) {
	if (!landing) return {};
	return {
		'data-landing': landing.phase,
		className: cn(
			landing.phase === 'flying' && 'opacity-0',
			landing.phase === 'landed' && !reduced && 'transition-opacity duration-150',
		),
	};
}

/**
 * What just landed, said once, inside the Monitor: a short label chip on the
 * landed item ("Recorded", "Needs you", "Denied · logged", "Agent paused"),
 * plus an arrival ring. Decoration: the act's narration caption announces it.
 */
function LandingMark({
	landing,
	reduced,
	className,
}: {
	landing: MonitorLanding | null | undefined;
	reduced: boolean;
	className?: string;
}) {
	const shown = landing?.phase === 'landed';
	return (
		<>
			{shown && !reduced && (
				<motion.span
					key={`ring-${landing.key}`}
					aria-hidden="true"
					data-testid="landing-ring"
					className={cn(
						'pointer-events-none absolute inset-0 rounded-[inherit] ring-2',
						landing.kind === 'denied' ? 'ring-danger/70' : 'ring-primary/70',
					)}
					initial={{ opacity: 0.9, scale: 1 }}
					animate={{ opacity: 0, scale: 1.04 }}
					transition={{ duration: 0.7, ease: 'easeOut' }}
				/>
			)}
			<AnimatePresence>
				{shown && (
					<motion.span
						key={landing.key}
						aria-hidden="true"
						data-testid="monitor-label"
						className={cn(
							'bg-card pointer-events-none absolute z-10 inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] leading-none font-medium whitespace-nowrap shadow-sm',
							CHIP_TONE[landing.kind],
							className,
						)}
						initial={reduced ? false : { opacity: 0, x: 10 }}
						animate={{ opacity: 1, x: 0 }}
						exit={reduced ? undefined : { opacity: 0, x: 6 }}
						transition={{ duration: reduced ? 0 : 0.25, ease: EASE_OUT_SOFT }}
					>
						{landing.text}
					</motion.span>
				)}
			</AnimatePresence>
		</>
	);
}

export function OperatorPanel({
	bell,
	log,
	agent,
	pointer = 'none',
	reduced = false,
	pulse = null,
	onHoverRow,
	landing = null,
	className,
}: {
	bell: BellItem[];
	log: ActivityRow[];
	/** The status row at the top; omitted → no row. */
	agent?: { name: string; status: OperatorAgentStatus };
	pointer?: OperatorPointer;
	reduced?: boolean;
	/** The row that just landed: it pulses with the lane's matching card. */
	pulse?: string | null;
	/** Pointer over a call row (its API), or `null` when it leaves. */
	onHoverRow?: (api: ActivityRow['api'] | null) => void;
	/** A Monitor hand-off in progress: its item is hidden in flight, labelled on landing. */
	landing?: MonitorLanding | null;
	className?: string;
}) {
	const [filter, setFilter] = useState<ActivityFilter>('all');
	const rows = (filter === 'all' ? log : log.filter((r) => r.source === filter)).slice(
		0,
		MAX_ROWS,
	);
	const at = (to: MonitorLanding['to']) => (landing?.to === to ? landing : null);
	const agentLanding = landingProps(at('agent'), reduced);
	return (
		<Card className={cn('flex flex-col', className)} data-testid="operator-panel">
			{agent && (
				<div
					className={cn(
						'border-border relative flex items-center gap-2 border-b px-4 py-3 transition-colors duration-300',
						agent.status === 'disabled' && 'bg-warning/[0.06]',
					)}
					data-testid="operator-agent"
					data-monitor-anchor="agent"
					data-landing={agentLanding['data-landing']}
				>
					<LandingMark
						landing={at('agent')}
						reduced={reduced}
						className="right-3 -bottom-2"
					/>
					<span className="bg-muted inline-flex h-7 w-7 items-center justify-center rounded-md">
						<Bot className="text-foreground h-4 w-4" aria-hidden="true" />
					</span>
					<span className="min-w-0 flex-1">
						<span className="text-foreground block font-mono text-xs">
							{agent.name}
						</span>
						<span
							className={cn(
								'block text-[11px]',
								AGENT_STATUS[agent.status ?? 'none'].cls,
							)}
						>
							{AGENT_STATUS[agent.status ?? 'none'].word}
						</span>
					</span>
					<Pointer show={pointer === 'pause'} reduced={reduced}>
						<span
							className={cn(
								'border-border inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[11px]',
								agent.status === 'disabled'
									? 'border-warning/60 bg-warning/15 text-foreground'
									: 'text-muted-foreground',
							)}
						>
							<Pause className="h-3 w-3" aria-hidden="true" />
							{agent.status === 'disabled' ? 'Paused' : 'Pause'}
						</span>
					</Pointer>
				</div>
			)}
			<section
				aria-label="Notifications"
				data-monitor-anchor="notifications"
				className={cn(
					'border-border border-b px-4 py-3 transition-colors duration-300',
					bell.length > 0 && 'bg-warning/[0.06]',
				)}
			>
				<div className="flex items-center justify-between gap-2">
					<h3 className="font-heading text-foreground text-sm font-semibold">
						Notifications
					</h3>
					<span className="text-muted-foreground relative inline-flex items-center gap-1.5 text-xs">
						<Bell className="h-4 w-4" aria-hidden="true" />
						<span data-testid="bell-count" className="font-mono tabular-nums">
							{bell.length}
						</span>
						<span className="sr-only">
							{bell.length === 1 ? 'needs you' : 'need you'}
						</span>
					</span>
				</div>
				<ul className="mt-2 space-y-2">
					{bell.length === 0 ? (
						<li className="text-muted-foreground text-xs">Nothing needs you.</li>
					) : (
						bell.map((item, i) => {
							const land = i === 0 ? landingProps(at('notifications'), reduced) : {};
							return (
								<li
									key={item.id}
									data-landing={land['data-landing']}
									className={cn(
										'border-warning/40 animate-rise relative flex items-start gap-2 rounded-lg border p-2',
										land.className,
									)}
								>
									{i === 0 && (
										<LandingMark
											landing={at('notifications')}
											reduced={reduced}
											className="top-1.5 right-1.5"
										/>
									)}
									<StatusGlyph tone="warn" label="Needs you" />
									<span className="min-w-0 flex-1 space-y-1.5">
										<span className="text-foreground block text-xs font-medium">
											{item.title}
										</span>
										<span className="text-muted-foreground block text-[11px]">
											{item.detail}
										</span>
										<span className="block">
											<Pointer show={pointer === 'approve'} reduced={reduced}>
												<Tag>{item.action}</Tag>
											</Pointer>
										</span>
									</span>
								</li>
							);
						})
					)}
				</ul>
			</section>

			<section aria-label="Activity" className="flex min-h-0 flex-1 flex-col px-4 py-3">
				<div className="flex flex-wrap items-center justify-between gap-2">
					<h3 className="font-heading text-foreground text-sm font-semibold">Activity</h3>
					<SegmentedToggle
						options={[...ACTIVITY_SOURCE_OPTIONS]}
						value={filter}
						onChange={setFilter}
						ariaLabel="Activity source"
					/>
				</div>
				<ul
					className="mt-2 space-y-1"
					data-testid="activity-rows"
					data-monitor-anchor="activity"
				>
					{rows.length === 0 ? (
						<li className="text-muted-foreground py-2 text-xs">
							{filter === 'jobs' ? 'No jobs ran in this story.' : 'Nothing yet.'}
						</li>
					) : (
						rows.map((row, i) => {
							const land = i === 0 ? landingProps(at('activity'), reduced) : {};
							return (
								<li
									key={row.id}
									data-landing={land['data-landing']}
									data-tone={row.tone}
									data-pulse={row.id === pulse || undefined}
									onMouseEnter={row.api ? () => onHoverRow?.(row.api) : undefined}
									onMouseLeave={row.api ? () => onHoverRow?.(null) : undefined}
									className={cn(
										'animate-arrive relative flex items-center gap-2 rounded-md px-1.5 py-1 transition-colors duration-300',
										row.id === pulse && 'bg-primary/10 ring-primary/60 ring-1',
										row.api && 'hover:bg-muted',
										land.className,
									)}
								>
									<StatusGlyph tone={row.tone} label={row.statusLabel} />
									{row.tag && (
										<span
											className="bg-primary text-primary-foreground inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full font-mono text-[9px] font-bold"
											title={`Call ${row.tag}`}
										>
											<span className="sr-only">Call </span>
											{row.tag}
										</span>
									)}
									{row.api && <VendorMark slug={row.api} size="xs" />}
									<span
										className={cn(
											'text-foreground min-w-0 flex-1 truncate text-xs',
											row.source === 'calls' && 'font-mono text-[11px]',
										)}
									>
										{row.title}
									</span>
									{row.detail && (
										<span
											className={cn(
												'shrink-0 font-mono text-[11px]',
												row.tone === 'fail'
													? 'text-danger'
													: 'text-muted-foreground',
											)}
										>
											{row.detail}
										</span>
									)}
									{i === 0 && (
										<LandingMark
											landing={at('activity')}
											reduced={reduced}
											className="top-1/2 right-0.5 -translate-y-1/2"
										/>
									)}
								</li>
							);
						})
					)}
				</ul>
			</section>
		</Card>
	);
}
