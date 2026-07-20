/**
 * Overview metric tiles for the LLM Proxy · Sessions surface.
 *
 * Five tiles aggregated ACROSS all sessions — deliberately NO "denied" tile
 * (denies show in the chart + on rows, per the plan §6.1). Composed from the
 * shared `Card` primitive; there is no `Stat` component in the design system.
 */
import { useMemo, type ComponentType } from 'react';
import { Activity, Cable, Coins, Cpu, PlayCircle, type LucideProps } from 'lucide-react';
import { Card } from '@/shared/ui';
import { formatCost, formatCount, formatTokens } from '@/modules/llm-proxy/lib/format';
import type { ProxySession } from '@/modules/llm-proxy/api';

interface Tile {
	key: string;
	label: string;
	value: string;
	hint: string;
	icon: ComponentType<LucideProps>;
	tint: string;
}

function aggregate(sessions: ProxySession[]) {
	const apis = new Set<string>();
	let calls = 0;
	let cost = 0;
	let tokens = 0;
	for (const s of sessions) {
		calls += s.tiles.calls;
		cost += s.tiles.cost_usd;
		tokens += s.tiles.tokens;
		for (const api of s.apis_touched) apis.add(api);
	}
	return { sessions: sessions.length, calls, cost, tokens, apis: apis.size };
}

export function MetricTiles({ sessions }: { sessions: ProxySession[] }) {
	const tiles = useMemo<Tile[]>(() => {
		const agg = aggregate(sessions);
		return [
			{
				key: 'sessions',
				label: 'Sessions',
				value: formatCount(agg.sessions),
				hint: 'Agent runs captured',
				icon: PlayCircle,
				tint: 'text-accent-blue bg-accent-blue/10',
			},
			{
				key: 'calls',
				label: 'Tool calls',
				value: formatCount(agg.calls),
				hint: 'Broker executions',
				icon: Activity,
				tint: 'text-accent-teal bg-accent-teal/10',
			},
			{
				key: 'apis',
				label: 'APIs touched',
				value: formatCount(agg.apis),
				hint: 'Distinct upstreams',
				icon: Cable,
				tint: 'text-primary bg-primary/10',
			},
			{
				key: 'cost',
				label: 'Est. cost',
				value: formatCost(agg.cost),
				hint: 'Estimated spend',
				icon: Coins,
				tint: 'text-accent-amber bg-accent-amber/10',
			},
			{
				key: 'tokens',
				label: 'Tokens',
				value: formatTokens(agg.tokens),
				hint: 'In + out combined',
				icon: Cpu,
				tint: 'text-accent-pink bg-accent-pink/10',
			},
		];
	}, [sessions]);

	return (
		<div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
			{tiles.map((tile) => {
				const Icon = tile.icon;
				return (
					<Card key={tile.key} className="p-4">
						<div className="flex items-start justify-between gap-2">
							<span className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
								{tile.label}
							</span>
							<span
								className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-lg ${tile.tint}`}
								aria-hidden="true"
							>
								<Icon className="h-4 w-4" />
							</span>
						</div>
						<p className="text-foreground mt-2 text-2xl font-semibold tabular-nums">
							{tile.value}
						</p>
						<p className="text-muted-foreground mt-0.5 text-xs">{tile.hint}</p>
					</Card>
				);
			})}
		</div>
	);
}
