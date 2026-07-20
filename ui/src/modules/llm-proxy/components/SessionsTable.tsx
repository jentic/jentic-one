/**
 * The Sessions table — one row per agent run. Rich columns: title + started,
 * agents count (with a tree glyph), tool-call count, a compact allow/deny mini
 * bar, API chips, est. cost, and a status badge. Row click opens the session
 * playground via the module's `sessionPath` link helper.
 *
 * The sessions list lacks per-outcome call counts, so the mini-bar approximates
 * governance: a single green "allow" bar, reddened with a thin deny slice only
 * for sessions whose copy/status implies a denial (matches the mock's one
 * denied call). Kept deliberately simple and honest.
 */
import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { GitBranch } from 'lucide-react';
import { Badge, type BadgeVariant, DataTable, type Column } from '@/shared/ui';
import { formatCost, formatCount, formatTimestamp } from '@/modules/llm-proxy/lib/format';
import { sessionPath } from '@/modules/llm-proxy/lib/links';
import type { ProxySession } from '@/modules/llm-proxy/api';

function hasDenial(session: ProxySession): boolean {
	return /den(y|ied|ial)/i.test(session.title) || session.status === 'denied';
}

const STATUS_VARIANT: Record<string, BadgeVariant> = {
	completed: 'success',
	running: 'pending',
	failed: 'danger',
	denied: 'warning',
};

function statusVariant(status: string): BadgeVariant {
	return STATUS_VARIANT[status] ?? 'default';
}

function OutcomeBar({ session }: { session: ProxySession }) {
	const total = Math.max(1, session.tiles.calls);
	const deny = hasDenial(session) ? Math.min(1, session.tiles.calls) : 0;
	const allow = session.tiles.calls - deny;
	const pct = (n: number) => `${(n / total) * 100}%`;
	return (
		<div className="flex items-center gap-2">
			<div
				className="bg-muted/50 flex h-1.5 w-24 overflow-hidden rounded-full"
				title={`${allow} allowed${deny > 0 ? `, ${deny} denied` : ''}`}
			>
				<div style={{ width: pct(allow), backgroundColor: '#5EDEB9' }} />
				{deny > 0 && <div style={{ width: pct(deny), backgroundColor: '#EDADAF' }} />}
			</div>
			<span className="text-muted-foreground text-xs tabular-nums">
				{formatCount(session.tiles.calls)}
			</span>
		</div>
	);
}

function ApiChips({ apis }: { apis: string[] }) {
	const shown = apis.slice(0, 2);
	const rest = apis.length - shown.length;
	return (
		<div className="flex flex-wrap items-center gap-1">
			{shown.map((api) => (
				<span
					key={api}
					className="bg-muted text-muted-foreground border-border/60 max-w-[9rem] truncate rounded-full border px-2 py-0.5 font-mono text-[10px]"
					title={api}
				>
					{api}
				</span>
			))}
			{rest > 0 && (
				<span className="text-muted-foreground text-[10px] font-medium">+{rest}</span>
			)}
		</div>
	);
}

export function SessionsTable({ sessions }: { sessions: ProxySession[] }) {
	const navigate = useNavigate();

	const columns = useMemo<Column<ProxySession>[]>(
		() => [
			{
				key: 'title',
				header: 'Session',
				render: (s) => (
					<div className="min-w-0">
						<p className="text-foreground truncate font-medium">{s.title}</p>
						<p className="text-muted-foreground text-xs">
							{formatTimestamp(s.started_at)}
						</p>
					</div>
				),
			},
			{
				key: 'agents',
				header: 'Agents',
				render: (s) => (
					<span className="text-foreground inline-flex items-center gap-1.5 text-sm tabular-nums">
						<GitBranch
							className="text-muted-foreground h-3.5 w-3.5"
							aria-hidden="true"
						/>
						{formatCount(s.tiles.agents)}
					</span>
				),
			},
			{
				key: 'calls',
				header: 'Tool calls',
				render: (s) => <OutcomeBar session={s} />,
			},
			{
				key: 'apis_touched',
				header: 'APIs',
				render: (s) => <ApiChips apis={s.apis_touched} />,
			},
			{
				key: 'cost_usd',
				header: 'Est. cost',
				className: 'text-right',
				render: (s) => (
					<span className="text-foreground tabular-nums">
						{formatCost(s.tiles.cost_usd)}
					</span>
				),
			},
			{
				key: 'status',
				header: 'Status',
				render: (s) => (
					<Badge variant={statusVariant(s.status)} dot>
						{s.status}
					</Badge>
				),
			},
		],
		[],
	);

	return (
		<DataTable
			columns={columns}
			data={sessions}
			getRowKey={(s) => s.id}
			onRowClick={(s) => navigate(sessionPath(s.id))}
			getRowLabel={(s) => `Open session ${s.title}`}
			ariaLabel="Agent sessions"
			emptyMessage="No sessions match your filters."
			renderCard={(s) => (
				<div className="space-y-2">
					<div className="flex items-start justify-between gap-2">
						<p className="text-foreground min-w-0 flex-1 truncate font-medium">
							{s.title}
						</p>
						<Badge variant={statusVariant(s.status)} dot>
							{s.status}
						</Badge>
					</div>
					<p className="text-muted-foreground text-xs">{formatTimestamp(s.started_at)}</p>
					<div className="flex items-center justify-between gap-2">
						<OutcomeBar session={s} />
						<span className="text-foreground text-xs tabular-nums">
							{formatCost(s.tiles.cost_usd)}
						</span>
					</div>
					<ApiChips apis={s.apis_touched} />
				</div>
			)}
		/>
	);
}
