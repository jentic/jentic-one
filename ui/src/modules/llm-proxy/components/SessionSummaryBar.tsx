/**
 * SessionSummaryBar — a compact, quiet one-row strip of session-level rollup
 * stats. Derived from the root agent's `rollup` (falling back to a sum over
 * `calls` if there is no root). Sits directly under the page header, above the
 * trace-flow, so you can read the whole run at a glance before diving in.
 */
import { motion } from 'framer-motion';
import { Activity, Boxes, Coins, Cpu, ShieldCheck, ShieldX, TriangleAlert } from 'lucide-react';
import { Badge } from '@/shared/ui';
import type { AgentStats, ProxyAgent, ProxyCall } from '@/modules/llm-proxy/api';
import { formatCost, formatTokens } from '@/modules/llm-proxy/lib/format';
import { cn } from '@/shared/lib/utils';

interface SessionSummaryBarProps {
	agents: ProxyAgent[];
	calls: ProxyCall[];
	className?: string;
}

const EMPTY: AgentStats = {
	calls: 0,
	allow: 0,
	deny: 0,
	error: 0,
	cost_usd: 0,
	tokens: 0,
	apis: [],
};

/** Prefer the root agent's rollup; otherwise reconstruct from the flat calls. */
function deriveStats(agents: ProxyAgent[], calls: ProxyCall[]): AgentStats {
	const root = agents.find((a) => a.parent_id === null) ?? agents[0];
	if (root && root.rollup.calls > 0) return root.rollup;

	const apis = new Set<string>();
	const acc = calls.reduce<AgentStats>(
		(s, c) => {
			if (c.api_name) apis.add(c.api_name);
			return {
				...s,
				calls: s.calls + 1,
				allow: s.allow + (c.verdict === 'allow' ? 1 : 0),
				deny: s.deny + (c.verdict === 'deny' ? 1 : 0),
				error: s.error + (c.status !== 'completed' && c.verdict === 'allow' ? 1 : 0),
				cost_usd: s.cost_usd + (c.cost_usd ?? 0),
				tokens: s.tokens + (c.tokens_in ?? 0) + (c.tokens_out ?? 0),
			};
		},
		{ ...EMPTY },
	);
	return { ...acc, apis: [...apis] };
}

function VerdictCount({
	icon,
	value,
	label,
	tone,
}: {
	icon: React.ReactNode;
	value: number;
	label: string;
	tone: 'success' | 'danger' | 'warning';
}) {
	const toneClass =
		tone === 'success' ? 'text-success' : tone === 'danger' ? 'text-danger' : 'text-warning';
	return (
		<span className="inline-flex items-center gap-1 tabular-nums" title={`${value} ${label}`}>
			<span className={cn('inline-flex items-center', toneClass)}>{icon}</span>
			<span className="text-foreground font-mono text-xs font-semibold">{value}</span>
			<span className="text-muted-foreground/70 hidden text-[11px] sm:inline">{label}</span>
		</span>
	);
}

function Stat({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
	return (
		<div className="flex items-center gap-2">
			<span className="text-muted-foreground/70">{icon}</span>
			<div className="leading-none">
				<div className="text-foreground font-mono text-sm font-semibold tabular-nums">
					{value}
				</div>
				<div className="text-muted-foreground/70 mt-0.5 text-[10px] tracking-wide uppercase">
					{label}
				</div>
			</div>
		</div>
	);
}

export function SessionSummaryBar({ agents, calls, className }: SessionSummaryBarProps) {
	const stats = deriveStats(agents, calls);
	const agentCount = agents.length;

	return (
		<motion.div
			initial={{ opacity: 0, y: 6 }}
			animate={{ opacity: 1, y: 0 }}
			transition={{ duration: 0.3, ease: 'easeOut' }}
			className={cn(
				'border-border/60 bg-card/60 flex flex-wrap items-center gap-x-6 gap-y-3 rounded-xl border px-4 py-3 backdrop-blur',
				className,
			)}
		>
			<Stat
				icon={<Activity className="h-4 w-4" />}
				label="Tool calls"
				value={stats.calls.toLocaleString()}
			/>

			<div className="bg-border/60 hidden h-8 w-px sm:block" aria-hidden="true" />

			<div className="flex items-center gap-3">
				<VerdictCount
					icon={<ShieldCheck className="h-3.5 w-3.5" />}
					value={stats.allow}
					label="allow"
					tone="success"
				/>
				<VerdictCount
					icon={<ShieldX className="h-3.5 w-3.5" />}
					value={stats.deny}
					label="deny"
					tone="danger"
				/>
				<VerdictCount
					icon={<TriangleAlert className="h-3.5 w-3.5" />}
					value={stats.error}
					label="error"
					tone="warning"
				/>
			</div>

			<div className="bg-border/60 hidden h-8 w-px sm:block" aria-hidden="true" />

			<Stat
				icon={<Boxes className="h-4 w-4" />}
				label="Agents"
				value={agentCount.toLocaleString()}
			/>
			<Stat
				icon={<Coins className="h-4 w-4" />}
				label="Est. cost"
				value={formatCost(stats.cost_usd)}
			/>
			<Stat
				icon={<Cpu className="h-4 w-4" />}
				label="Tokens"
				value={formatTokens(stats.tokens)}
			/>

			{stats.apis.length > 0 && (
				<>
					<div className="bg-border/60 hidden h-8 w-px sm:block" aria-hidden="true" />
					<div className="flex flex-wrap items-center gap-1.5">
						<span className="text-muted-foreground/70 text-[10px] tracking-wide uppercase">
							APIs
						</span>
						{stats.apis.map((api) => (
							<Badge key={api} variant="default">
								{api}
							</Badge>
						))}
					</div>
				</>
			)}
		</motion.div>
	);
}
