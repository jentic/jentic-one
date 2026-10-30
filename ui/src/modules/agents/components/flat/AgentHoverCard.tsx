/**
 * The strip's hover card: who an agent is and what it reaches, on hover and on
 * keyboard focus of its tab or circle. Rendered as `Tooltip` content, so it
 * rides the shared bubble (portal, viewport clamp, Esc, `aria-describedby`).
 * Only facts the page already holds are shown; an unknown count is left out.
 */
import { ArrowDown, Check, ShieldQuestion } from 'lucide-react';
import { AgentBadge, STATUS_ICON, STATUS_LABELS, STATUS_TINT } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import type { AgentEntity } from '@/modules/agents/api';

export interface AgentFacts {
	apiCount?: number;
	credentialCount?: number;
	blocked?: number;
	gaps?: number;
	/** Held calls waiting for the viewer's decision. */
	waiting?: number;
}

/** One fact column; `attention` marks the one that asks for the viewer. */
interface FactRow {
	label: string;
	value: string;
	attention?: boolean;
}

export function AgentHoverCard({
	agent,
	facts,
	selected,
}: {
	agent: AgentEntity;
	facts: AgentFacts;
	selected: boolean;
}) {
	const StatusIcon = STATUS_ICON[agent.status];
	const rows: FactRow[] = [];
	if (facts.apiCount != null) rows.push({ label: 'APIs', value: String(facts.apiCount) });
	if (facts.credentialCount != null) {
		rows.push({ label: 'Credentials', value: String(facts.credentialCount) });
	}
	if (facts.blocked) rows.push({ label: 'Blocked', value: String(facts.blocked) });
	if (facts.gaps) rows.push({ label: 'To set up', value: String(facts.gaps) });
	// The calls held for the viewer's decision, in the same amber as the tab's
	// chip and the card's status mark.
	if (facts.waiting) {
		rows.push({ label: 'Waiting for you', value: String(facts.waiting), attention: true });
	}
	return (
		<span
			data-testid="agent-hover-card"
			className="flex w-60 max-w-full flex-col gap-2.5 py-0.5"
		>
			<span className="flex min-w-0 items-center gap-2.5">
				{/* Decorative: the card is also the trigger's description text. */}
				<span aria-hidden="true" className="inline-flex">
					<AgentBadge id={agent.id} name={agent.name} size="sm" shape="circle" />
				</span>
				<span className="min-w-0">
					<span className="text-foreground-name block text-[13px] font-semibold [overflow-wrap:anywhere]">
						{agent.name}
					</span>
					<span className="text-foreground-sub mt-0.5 flex items-center gap-1 text-[11.5px] font-medium">
						<StatusIcon
							aria-hidden="true"
							className={cn('size-3', STATUS_TINT[agent.status])}
						/>
						{agent.status === 'pending'
							? 'Awaiting approval'
							: STATUS_LABELS[agent.status]}
					</span>
				</span>
			</span>
			{rows.length > 0 && (
				<span className="flex gap-4">
					{rows.map(({ label, value, attention }) => (
						<span key={label} className="flex flex-col">
							<span
								className={cn(
									'text-[10px] font-semibold tracking-[0.04em] uppercase',
									attention ? 'text-warning' : 'text-foreground-faint',
								)}
							>
								{label}
							</span>
							<span
								className={cn(
									'inline-flex items-center gap-1 text-[13px] font-semibold tabular-nums',
									attention ? 'text-warning' : 'text-foreground-name',
								)}
							>
								{attention && (
									<ShieldQuestion aria-hidden="true" className="size-3" />
								)}
								{value}
							</span>
						</span>
					))}
				</span>
			)}
			<span className="text-foreground-faint border-hairline flex items-center gap-1.5 border-t pt-2 text-[11px]">
				{selected ? (
					<>
						<Check aria-hidden="true" className="size-3" />
						Selected — its details are below
					</>
				) : (
					<>
						<ArrowDown aria-hidden="true" className="size-3" />
						Click to switch
					</>
				)}
			</span>
		</span>
	);
}
