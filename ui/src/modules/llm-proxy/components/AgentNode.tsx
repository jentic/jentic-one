/**
 * AgentNode — a card-like node in the trace-flow representing one agent.
 *
 * Shows the agent name, its subagent_type, and a compact tool-call SUMMARY: the
 * total call count, a small HTTP-method breakdown (e.g. "GET 8 · POST 4") and the
 * allow/deny/error split (green/red/amber). Subtly tinted by health: any denies
 * → red tint, any errors → amber tint, otherwise green. The agent's actual tool
 * calls render as a horizontal arrow chain in the SAME ROW to the RIGHT of the
 * card (see `CallChain`); a per-agent "Hide calls / Calls" toggle in the footer
 * collapses that chain independently of the "Workers" child-agent toggle.
 *
 * On hover (or keyboard focus) it shows a compact floating stats card (rollup:
 * calls, allow/deny/error, cost, tokens, APIs). The card is smart-positioned
 * ADJACENT to the node — to the right by default, flipping to the left near the
 * viewport's right edge and clamped vertically — so it never blankets sibling
 * nodes. It is `pointer-events-none` and transient (hides on leave / blur). Only
 * one card shows at a time (the "which agent is hovered" state lives in
 * TraceFlow). A small "chat" affordance opens the session's chat.
 */
import { useLayoutEffect, useMemo, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { ChevronDown, ChevronRight, MessageSquare } from 'lucide-react';
import { Badge } from '@/shared/ui';
import type { AgentStats, ProxyAgent, ProxyCall } from '@/modules/llm-proxy/api';
import { formatCost, formatTokens } from '@/modules/llm-proxy/lib/format';
import { cn } from '@/shared/lib/utils';

type Health = 'clean' | 'error' | 'denied';

function healthOf(stats: AgentStats): Health {
	if (stats.deny > 0) return 'denied';
	if (stats.error > 0) return 'error';
	return 'clean';
}

const HEALTH_CARD: Record<Health, string> = {
	clean: 'border-success/30 bg-success/[0.06]',
	error: 'border-warning/40 bg-warning/[0.07]',
	denied: 'border-danger/40 bg-danger/[0.07]',
};

const HEALTH_ACCENT: Record<Health, string> = {
	clean: 'bg-success',
	error: 'bg-warning',
	denied: 'bg-danger',
};

/** A tiny allow/deny/error proportion bar. */
function DotBar({
	split,
	className,
}: {
	split: { allow: number; deny: number; error: number };
	className?: string;
}) {
	const total = Math.max(1, split.allow + split.deny + split.error);
	const segs: Array<[string, number]> = [
		['bg-success', split.allow],
		['bg-danger', split.deny],
		['bg-warning', split.error],
	];
	return (
		<div
			className={cn('bg-muted flex h-1.5 w-full overflow-hidden rounded-full', className)}
			aria-hidden="true"
		>
			{segs.map(([color, n], i) =>
				n > 0 ? (
					<span key={i} className={color} style={{ width: `${(n / total) * 100}%` }} />
				) : null,
			)}
		</div>
	);
}

/** Small ordered HTTP-method histogram, e.g. "GET 8 · POST 4". */
function methodBreakdown(calls: ProxyCall[]): Array<[string, number]> {
	const counts = new Map<string, number>();
	for (const c of calls) {
		const m = (c.method || 'OTHER').toUpperCase();
		counts.set(m, (counts.get(m) ?? 0) + 1);
	}
	return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

/**
 * Allow/deny/error split for THIS agent's own calls, derived from the call rows
 * (not `agent.stats`, which is zero for orchestrators that only spawn workers).
 */
function verdictSplit(calls: ProxyCall[]): { allow: number; deny: number; error: number } {
	let allow = 0;
	let deny = 0;
	let error = 0;
	for (const c of calls) {
		if (c.verdict === 'deny' || c.status === 'denied') deny += 1;
		else if (c.status !== 'completed') error += 1;
		else allow += 1;
	}
	return { allow, deny, error };
}

interface AgentNodeProps {
	agent: ProxyAgent;
	/** This agent's own tool calls (drives the compact on-card summary). */
	calls: ProxyCall[];
	/** True when this node has collapsible child subagents. */
	hasChildren?: boolean;
	expanded?: boolean;
	onToggleExpand?: () => void;
	/** Open the chat drawer for this agent / session. */
	onOpenChat?: (agent: ProxyAgent) => void;
	/** Select this agent (plain click on the card body) — opens its detail drawer. */
	onSelect?: (agent: ProxyAgent) => void;
	/** True when this node's stats card should be shown (owned by TraceFlow). */
	showCard?: boolean;
	/** Request to show this node's card (hover / focus). */
	onShowCard?: () => void;
	/** Request to hide this node's card (leave / blur). */
	onHideCard?: () => void;
	/** Prominent, larger presentation for a single-agent session. */
	prominent?: boolean;
	/** True when this agent made at least one tool call (drives the calls toggle). */
	hasCalls?: boolean;
	/** True when this agent's own tool-call chain is collapsed (hidden). */
	callsCollapsed?: boolean;
	/** Toggle this agent's own tool-call chain visibility. */
	onToggleCalls?: () => void;
}

export function AgentNode({
	agent,
	calls,
	hasChildren = false,
	expanded = false,
	onToggleExpand,
	onOpenChat,
	onSelect,
	showCard = false,
	onShowCard,
	onHideCard,
	prominent = false,
	hasCalls = false,
	callsCollapsed = false,
	onToggleCalls,
}: AgentNodeProps) {
	const health = healthOf(agent.rollup);
	const rollup = agent.rollup;
	const split = useMemo(() => verdictSplit(calls), [calls]);
	const methods = useMemo(() => methodBreakdown(calls), [calls]);
	const callCount = calls.length;

	// Smart hover-card placement: default to the RIGHT of the card, flip to the
	// LEFT when the card sits near the viewport's right edge, and nudge the card
	// vertically so it never spills past the top/bottom of the viewport. Measured
	// off the card's live rect so it never blankets sibling nodes.
	const HOVER_W = 232;
	const HOVER_GAP = 12;
	const cardRef = useRef<HTMLDivElement | null>(null);
	const [place, setPlace] = useState<{ side: 'right' | 'left'; top: number }>({
		side: 'right',
		top: 0,
	});
	useLayoutEffect(() => {
		if (!showCard || !cardRef.current) return;
		const r = cardRef.current.getBoundingClientRect();
		const spaceRight = window.innerWidth - r.right;
		const side: 'right' | 'left' = spaceRight >= HOVER_W + HOVER_GAP + 8 ? 'right' : 'left';
		// Vertically align the card's top with the node, then clamp within view
		// (assume a ~ 200px tall popover; clamp so neither edge overflows).
		const approxH = 200;
		let top = 0;
		const nodeTopVp = r.top;
		if (nodeTopVp + approxH > window.innerHeight - 8) {
			top = window.innerHeight - 8 - approxH - nodeTopVp;
		}
		if (nodeTopVp + top < 8) top = 8 - nodeTopVp;
		setPlace({ side, top });
	}, [showCard]);

	return (
		<div
			ref={cardRef}
			className="relative"
			onMouseEnter={onShowCard}
			onMouseLeave={onHideCard}
			onFocus={onShowCard}
			onBlur={onHideCard}
		>
			<motion.div
				initial={{ opacity: 0, x: -8 }}
				animate={{ opacity: 1, x: 0 }}
				transition={{ duration: 0.25, ease: 'easeOut' }}
				onClick={
					onSelect
						? (e) => {
								// Ignore clicks that land on a footer control (chat / workers /
								// calls) so those keep their own behaviour. We match only real
								// controls (button/link), NOT the card-root role="button" — the
								// card body itself is the click target that opens the drawer.
								if ((e.target as HTMLElement).closest('button, a')) return;
								onSelect(agent);
							}
						: undefined
				}
				role={onSelect ? 'button' : undefined}
				tabIndex={onSelect ? 0 : undefined}
				onKeyDown={
					onSelect
						? (e) => {
								if (e.key === 'Enter' || e.key === ' ') {
									e.preventDefault();
									onSelect(agent);
								}
							}
						: undefined
				}
				aria-label={onSelect ? `Open ${agent.name} detail` : undefined}
				className={cn(
					'rounded-xl border p-3 shadow-sm transition-colors',
					prominent ? 'w-[320px] p-4' : 'w-[240px]',
					onSelect &&
						'hover:border-accent-blue/50 focus-visible:ring-ring cursor-pointer focus-visible:ring-2 focus-visible:outline-none',
					HEALTH_CARD[health],
				)}
			>
				<div className="flex items-start gap-2">
					<span
						className={cn('mt-1 h-2 w-2 shrink-0 rounded-full', HEALTH_ACCENT[health])}
						aria-hidden="true"
					/>
					<div className="min-w-0 flex-1">
						<div
							className={cn(
								'text-foreground truncate font-semibold',
								prominent ? 'text-base' : 'text-sm',
							)}
						>
							{agent.name}
						</div>
						<div className="text-muted-foreground/80 mt-0.5 flex items-center gap-1.5 text-[10px]">
							<span className="bg-muted rounded px-1 py-0.5 font-mono tracking-wide">
								{agent.subagent_type}
							</span>
							<span className="tabular-nums">depth {agent.depth}</span>
						</div>
					</div>
				</div>

				{/* Compact tool-call summary: count · method breakdown · verdict split. */}
				<div className="mt-2.5 space-y-1.5">
					<div className="flex items-center justify-between text-[11px]">
						<span className="text-muted-foreground">
							{callCount} call{callCount === 1 ? '' : 's'}
						</span>
						<span className="flex items-center gap-1.5 font-mono tabular-nums">
							<span className="text-success" title="Allowed">
								{split.allow}
							</span>
							<span className="text-danger" title="Denied">
								{split.deny}
							</span>
							<span className="text-warning" title="Errored">
								{split.error}
							</span>
						</span>
					</div>
					{methods.length > 0 && (
						<div className="text-muted-foreground/90 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 font-mono text-[10px]">
							{methods.map(([m, n], i) => (
								<span key={m} className="inline-flex items-center">
									{i > 0 && <span className="text-border mr-1.5">·</span>}
									<span className="text-foreground/70 font-semibold">{m}</span>
									<span className="ml-0.5 tabular-nums">{n}</span>
								</span>
							))}
						</div>
					)}
					<DotBar split={split} />
				</div>

				{(onOpenChat || hasChildren || hasCalls) && (
					<div className="border-border/40 mt-2.5 flex items-center justify-between gap-2 border-t pt-2">
						{onOpenChat ? (
							<button
								type="button"
								onClick={() => onOpenChat(agent)}
								className="text-muted-foreground hover:text-foreground focus-visible:ring-ring inline-flex items-center gap-1 rounded text-[11px] transition-colors focus-visible:ring-2 focus-visible:outline-none"
							>
								<MessageSquare className="h-3 w-3" />
								Chat
							</button>
						) : (
							<span />
						)}
						<div className="flex items-center gap-2.5">
							{hasCalls && (
								<button
									type="button"
									onClick={onToggleCalls}
									className="text-muted-foreground hover:text-foreground focus-visible:ring-ring inline-flex items-center gap-0.5 rounded text-[11px] transition-colors focus-visible:ring-2 focus-visible:outline-none"
									aria-expanded={!callsCollapsed}
									title={callsCollapsed ? 'Show tool calls' : 'Hide tool calls'}
								>
									{callsCollapsed ? (
										<ChevronRight className="h-3 w-3" />
									) : (
										<ChevronDown className="h-3 w-3" />
									)}
									{callsCollapsed ? 'Calls' : 'Hide calls'}
								</button>
							)}
							{hasChildren && (
								<button
									type="button"
									onClick={onToggleExpand}
									className="text-muted-foreground hover:text-foreground focus-visible:ring-ring inline-flex items-center gap-0.5 rounded text-[11px] transition-colors focus-visible:ring-2 focus-visible:outline-none"
									aria-expanded={expanded}
								>
									{expanded ? (
										<ChevronDown className="h-3 w-3" />
									) : (
										<ChevronRight className="h-3 w-3" />
									)}
									{expanded ? 'Collapse' : 'Workers'}
								</button>
							)}
						</div>
					</div>
				)}
			</motion.div>

			{showCard && (
				<motion.div
					initial={{ opacity: 0, scale: 0.98 }}
					animate={{ opacity: 1, scale: 1 }}
					transition={{ duration: 0.12 }}
					role="tooltip"
					aria-label={`${agent.name} statistics`}
					style={{
						width: HOVER_W,
						top: place.top,
						...(place.side === 'right'
							? { left: '100%', marginLeft: HOVER_GAP }
							: { right: '100%', marginRight: HOVER_GAP }),
					}}
					className="border-border bg-card pointer-events-none absolute z-40 rounded-xl border p-3 shadow-xl"
				>
					<div className="text-foreground mb-2 truncate text-xs font-semibold">
						{agent.name}
					</div>
					<div className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px]">
						<Stat label="Calls" value={String(rollup.calls)} />
						<Stat label="Allow" value={String(rollup.allow)} tone="text-success" />
						<Stat label="Deny" value={String(rollup.deny)} tone="text-danger" />
						<Stat label="Error" value={String(rollup.error)} tone="text-warning" />
						<Stat label="Cost" value={formatCost(rollup.cost_usd)} />
						<Stat label="Tokens" value={formatTokens(rollup.tokens)} />
					</div>
					{rollup.apis.length > 0 && (
						<div className="border-border/40 mt-2 flex flex-wrap gap-1 border-t pt-2">
							{rollup.apis.map((api) => (
								<Badge key={api} variant="default">
									{api}
								</Badge>
							))}
						</div>
					)}
				</motion.div>
			)}
		</div>
	);
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
	return (
		<div className="flex items-baseline justify-between gap-2">
			<span className="text-muted-foreground">{label}</span>
			<span className={cn('font-mono font-semibold tabular-nums', tone ?? 'text-foreground')}>
				{value}
			</span>
		</div>
	);
}
