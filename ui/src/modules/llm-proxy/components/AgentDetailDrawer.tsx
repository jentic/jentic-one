/**
 * AgentDetailDrawer — the drill-in for a single agent / subagent node.
 *
 * Opens from a plain (non-drag) node click in the trace-flow. Surfaces the
 * agent's full picture with progressive disclosure: identity + the own-vs-rollup
 * stat split are always visible (an orchestrator shows 0 own calls but a large
 * rollup); the body is this agent's own tool calls and its chat turns, each a
 * clickable row that hands off to the existing CallDetailDrawer / ChatTurnDrawer.
 */
import { Bot, MessageSquare, X } from 'lucide-react';
import { SheetPrimitive, Badge, MethodBadge, CopyButton } from '@/shared/ui';
import type { ChatTurn, ProxyAgent, ProxyCall } from '@/modules/llm-proxy/api';
import {
	formatCost,
	formatDuration,
	formatTimestamp,
	formatTokens,
} from '@/modules/llm-proxy/lib/format';
import { callTone } from '@/modules/llm-proxy/components/CallBlock';
import { cn } from '@/shared/lib/utils';

interface AgentDetailDrawerProps {
	agent: ProxyAgent | null;
	/** This agent's own tool calls (already filtered to agent.id by the caller). */
	calls: ProxyCall[];
	/** This agent's chat turns (already filtered to agent.id by the caller). */
	turns: ChatTurn[];
	/** The parent agent, if this node has one (for the "spawned by" label). */
	parent: ProxyAgent | null;
	open: boolean;
	onClose: () => void;
	onOpenCall: (call: ProxyCall) => void;
	onOpenTurn: (turn: ChatTurn) => void;
	/** Portal target — the fullscreen element when the canvas is fullscreen. */
	container?: HTMLElement | null;
}

function Field({
	label,
	children,
	className,
}: {
	label: string;
	children: React.ReactNode;
	className?: string;
}) {
	return (
		<div className={className}>
			<div className="text-muted-foreground/70 mb-1 text-[10px] font-semibold tracking-wide uppercase">
				{label}
			</div>
			<div className="text-foreground text-sm">{children}</div>
		</div>
	);
}

function Mono({ children }: { children: React.ReactNode }) {
	return <span className="font-mono text-xs break-all">{children}</span>;
}

const TONE_TEXT = {
	allow: 'text-success',
	deny: 'text-danger',
	error: 'text-warning',
} as const;

/** One labelled stat block ("This agent" vs "+ descendants"). */
function StatGroup({
	label,
	sub,
	stats,
}: {
	label: string;
	sub: string;
	stats: ProxyAgent['stats'];
}) {
	return (
		<div className="border-border/50 rounded-lg border p-3">
			<div className="mb-2">
				<div className="text-foreground text-xs font-semibold">{label}</div>
				<div className="text-muted-foreground/70 text-[10px]">{sub}</div>
			</div>
			<div className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px]">
				<Stat label="Calls" value={String(stats.calls)} />
				<Stat label="Allow" value={String(stats.allow)} tone="text-success" />
				<Stat label="Deny" value={String(stats.deny)} tone="text-danger" />
				<Stat label="Error" value={String(stats.error)} tone="text-warning" />
				<Stat label="Tokens" value={formatTokens(stats.tokens)} />
				<Stat label="Est. cost" value={formatCost(stats.cost_usd)} />
			</div>
			<div className="border-border/40 mt-2 border-t pt-2">
				<div className="text-muted-foreground/70 mb-1 text-[10px] font-semibold tracking-wide uppercase">
					APIs
				</div>
				{stats.apis.length > 0 ? (
					<div className="flex flex-wrap gap-1">
						{stats.apis.map((api) => (
							<Badge key={api} variant="default">
								{api}
							</Badge>
						))}
					</div>
				) : (
					<span className="text-muted-foreground text-[11px]">—</span>
				)}
			</div>
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

export function AgentDetailDrawer({
	agent,
	calls,
	turns,
	parent,
	open,
	onClose,
	onOpenCall,
	onOpenTurn,
	container,
}: AgentDetailDrawerProps) {
	return (
		<SheetPrimitive
			open={open}
			onClose={onClose}
			side="right"
			ariaLabel="Agent detail"
			className="sm:w-[560px]"
			container={container}
		>
			{agent && (
				<div className="flex h-full flex-col">
					<div className="border-border/60 flex items-start justify-between gap-3 border-b px-5 py-4">
						<div className="flex min-w-0 items-start gap-2">
							<Bot className="text-accent-blue mt-0.5 h-5 w-5 shrink-0" />
							<div className="min-w-0">
								<div className="mb-1 flex flex-wrap items-center gap-2">
									<Badge variant="default" dot={agent.role === 'main'}>
										{agent.role === 'main' ? 'orchestrator' : 'subagent'}
									</Badge>
									<Badge variant="default">
										<span className="font-mono">{agent.subagent_type}</span>
									</Badge>
									<span className="text-muted-foreground text-[11px] tabular-nums">
										depth {agent.depth}
									</span>
								</div>
								<h2 className="text-foreground text-base font-semibold break-words">
									{agent.name}
								</h2>
								{parent ? (
									<p className="text-muted-foreground mt-0.5 text-xs">
										spawned by{' '}
										<span className="text-foreground font-medium">
											{parent.name}
										</span>
									</p>
								) : (
									<p className="text-muted-foreground mt-0.5 text-xs">
										root agent — no parent
									</p>
								)}
							</div>
						</div>
						<button
							type="button"
							onClick={onClose}
							aria-label="Close"
							className="text-muted-foreground hover:text-foreground shrink-0 rounded p-1 transition-colors"
						>
							<X className="h-4 w-4" />
						</button>
					</div>

					<div className="flex-1 space-y-5 overflow-y-auto px-5 py-4">
						{/* Identity */}
						<div className="grid grid-cols-2 gap-4">
							<Field label="Agent id">
								<div className="flex items-center gap-1">
									<Mono>{agent.id}</Mono>
									<CopyButton
										value={agent.id}
										variant="ghost"
										size="icon"
										ariaLabel="Copy agent id"
									/>
								</div>
							</Field>
							<Field label="Actor id">
								<div className="flex items-center gap-1">
									<Mono>{agent.actor_id}</Mono>
									<CopyButton
										value={agent.actor_id}
										variant="ghost"
										size="icon"
										ariaLabel="Copy actor id"
									/>
								</div>
							</Field>
							<Field label="Spawned" className="col-span-2">
								{formatTimestamp(agent.spawned_at)}
							</Field>
						</div>

						{/* Own vs rollup — the key distinction. */}
						<div className="grid grid-cols-2 gap-3">
							<StatGroup
								label="This agent"
								sub="its own tool calls"
								stats={agent.stats}
							/>
							<StatGroup
								label="+ descendants"
								sub="this agent + subagents"
								stats={agent.rollup}
							/>
						</div>

						{/* Own calls */}
						<div>
							<div className="text-muted-foreground/70 mb-2 text-[10px] font-semibold tracking-wide uppercase">
								Tool calls ({calls.length})
							</div>
							{calls.length > 0 ? (
								<ul className="space-y-1.5">
									{calls.map((call) => {
										const tone = callTone(call);
										return (
											<li key={call.call_id}>
												<button
													type="button"
													onClick={() => onOpenCall(call)}
													className="border-border/50 bg-card/60 hover:bg-muted/40 hover:border-border focus-visible:ring-ring flex w-full items-center gap-2 rounded-lg border px-2.5 py-2 text-left transition-colors focus-visible:ring-2 focus-visible:outline-none"
												>
													<span
														className={cn(
															'h-2 w-2 shrink-0 rounded-full',
															tone === 'allow'
																? 'bg-success'
																: tone === 'deny'
																	? 'bg-danger'
																	: 'bg-warning',
														)}
														aria-hidden="true"
													/>
													<MethodBadge method={call.method} />
													<span className="text-foreground min-w-0 flex-1 truncate font-mono text-[11px]">
														{call.path}
													</span>
													<span
														className={cn(
															'shrink-0 text-[10px] font-semibold uppercase',
															TONE_TEXT[tone],
														)}
													>
														{tone}
													</span>
													<span className="text-muted-foreground shrink-0 text-[10px] tabular-nums">
														{formatDuration(call.duration_ms)}
													</span>
												</button>
											</li>
										);
									})}
								</ul>
							) : (
								<p className="text-muted-foreground border-border/40 rounded-lg border border-dashed px-3 py-2 text-xs italic">
									No tool calls — orchestration only.
								</p>
							)}
						</div>

						{/* Chat turns */}
						<div>
							<div className="text-muted-foreground/70 mb-2 text-[10px] font-semibold tracking-wide uppercase">
								Chat turns ({turns.length})
							</div>
							{turns.length > 0 ? (
								<ul className="space-y-1.5">
									{turns.map((turn) => (
										<li key={turn.turn_id}>
											<button
												type="button"
												onClick={() => onOpenTurn(turn)}
												className="border-border/50 bg-card/60 hover:bg-muted/40 hover:border-border focus-visible:ring-ring flex w-full items-start gap-2 rounded-lg border px-2.5 py-2 text-left transition-colors focus-visible:ring-2 focus-visible:outline-none"
											>
												<MessageSquare className="text-accent-blue mt-0.5 h-3.5 w-3.5 shrink-0" />
												<span className="min-w-0 flex-1">
													<span className="text-foreground block truncate text-xs">
														{turn.assistant_text ||
															turn.first_user_msg ||
															turn.turn_id}
													</span>
													<span className="text-muted-foreground mt-0.5 block font-mono text-[10px]">
														{turn.turn_id}
														{turn.tool_uses.length > 0 &&
															` · ${turn.tool_uses.length} tool use${turn.tool_uses.length === 1 ? '' : 's'}`}
													</span>
												</span>
											</button>
										</li>
									))}
								</ul>
							) : (
								<p className="text-muted-foreground border-border/40 rounded-lg border border-dashed px-3 py-2 text-xs italic">
									No chat turns recorded for this agent.
								</p>
							)}
						</div>
					</div>
				</div>
			)}
		</SheetPrimitive>
	);
}
