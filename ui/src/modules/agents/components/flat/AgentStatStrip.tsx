/**
 * AgentStatStrip — the selected agent's vitals as ONE quiet meta line under the
 * `APIs · N` heading ("2 configured · 1 to set up · 181 operations · 2
 * credentials · 1,204 calls in 7d"), not a figure board. Per clause: `undefined`
 * renders a skeleton, `null` (gated or failed) OMITS it, and nothing provable
 * renders nothing at all.
 */
import { Fragment } from 'react';
import { Skeleton } from '@/shared/ui';
import { cn, timeAgo } from '@/shared/lib/utils';
import type { ActorUsageDetail } from '@/modules/agents/api';
import { successShare } from '@/modules/agents/components/detail/shared';
import type { ApiTileStats } from '@/modules/agents/lib/apiTiles';

interface AgentStatStripProps {
	/** The agent named in the line's accessible label. */
	agentName: string;
	/** Tile-composition stats (same math as the grid): `undefined` while the join
	 * loads or drains, `null` when it failed — the clause drops rather than print a
	 * wrong number. */
	access: ApiTileStats | null | undefined;
	/** Bound-credential count off the already-fetched bindings list. */
	credentialCount: number | null | undefined;
	/** 7-day usage rollup; `null` = admin-gated (403) or failed → omitted. */
	usage: ActorUsageDetail | null | undefined;
	/** Most-recent-execution timestamp: `{ at: null }` means the feed loaded empty
	 * (em-dash); `null` means the feed is gated or failed (omit). */
	lastActivity: { at: string | null } | null | undefined;
}

/** One clause on the line. `text: undefined` renders the skeleton. */
interface Clause {
	key: string;
	text: string | undefined;
	tone?: 'warning' | 'danger';
}

const TONE_CLASS: Record<NonNullable<Clause['tone']>, string> = {
	warning: 'text-warning',
	danger: 'text-danger',
};

export function AgentStatStrip({
	agentName,
	access,
	credentialCount,
	usage,
	lastActivity,
}: AgentStatStripProps) {
	const clauses: Clause[] = [];

	// Setup clauses — the grid's own math. "to set up" appears (warning-tinted)
	// only when the drained join PROVES a gap.
	if (access !== null) {
		clauses.push({ key: 'configured', text: access && `${access.configured} configured` });
		if (access && access.needsSetup > 0) {
			clauses.push({
				key: 'needs-setup',
				text: `${access.needsSetup} to set up`,
				tone: 'warning',
			});
		}
		// "reachable" is load-bearing: the figure excludes paused bindings, so a bare
		// `0 operations` beside a tile advertising 900 would read as a contradiction.
		// A partly provable count takes the `+` that says the sum is only a floor.
		if (access?.operations !== null) {
			clauses.push({
				key: 'operations',
				text:
					access &&
					`${access.operations.toLocaleString()}${access.operationsAtLeast ? '+' : ''} operations reachable`,
			});
		}
	}
	if (credentialCount !== null) {
		clauses.push({
			key: 'credentials',
			text:
				credentialCount === undefined
					? undefined
					: `${credentialCount} ${credentialCount === 1 ? 'credential' : 'credentials'}`,
		});
	}

	// Activity clauses — the 7-day usage rollup and the newest execution.
	if (usage !== null) {
		clauses.push({
			key: 'executions',
			text: usage && `${usage.total.toLocaleString()} calls in 7d`,
		});
		const share = usage ? successShare(usage.success, usage.total) : undefined;
		const unhealthy = usage ? usage.total > 0 && usage.success / usage.total < 0.95 : false;
		clauses.push({
			key: 'success-rate',
			// A zero-traffic rate has nothing to judge — the clause inverts so
			// the em-dash lands where the figure would.
			text:
				share === undefined
					? undefined
					: share === '—'
						? 'success rate —'
						: `${share} success`,
			tone: unhealthy ? 'danger' : undefined,
		});
	}
	if (lastActivity !== null) {
		clauses.push({
			key: 'last-activity',
			text:
				lastActivity === undefined
					? undefined
					: lastActivity.at
						? `last used ${timeAgo(lastActivity.at)}`
						: 'last used —',
		});
	}

	if (clauses.length === 0) return null;

	return (
		<div
			role="group"
			aria-label={`${agentName} stats`}
			data-testid="agent-stat-strip"
			className="text-muted-foreground flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs"
		>
			{clauses.map((clause, index) => (
				<Fragment key={clause.key}>
					{index > 0 && (
						<span aria-hidden="true" className="text-border">
							·
						</span>
					)}
					<span
						data-testid={`stat-${clause.key}`}
						className={cn(
							'whitespace-nowrap tabular-nums',
							clause.tone && TONE_CLASS[clause.tone],
						)}
					>
						{clause.text === undefined ? (
							<Skeleton className="inline-block h-3 w-20 align-middle" />
						) : (
							clause.text
						)}
					</span>
				</Fragment>
			))}
		</div>
	);
}
