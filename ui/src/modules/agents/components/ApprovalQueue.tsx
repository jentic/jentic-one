/**
 * ApprovalQueue — the warning band above the fleet table listing actors
 * awaiting an operator decision, with one-click Approve/Deny per row.
 *
 * The urgent slice of the page keeps top billing (self-registered agents are
 * inert until someone decides), but shares the page's table grammar instead
 * of owning a separate roster layout: compact identity (badge + name link +
 * mono id + registered time) and the two decision buttons. Denials confirm
 * via the page-level `DenyDialog` (routed through `onAction`), approvals
 * fire immediately — same contract as the table's kebab menu.
 */
import { AgentBadge, AppLink, Button, Card } from '@/shared/ui';
import { formatTimestamp, timeAgo } from '@/shared/lib/utils';
import { ACTION_LABEL, ACTION_VARIANT, type AgentAction } from '@/modules/agents/api';
import type { ActorRow } from '@/modules/agents/components/ActorTable';

interface ApprovalQueueProps<T extends ActorRow> {
	items: T[];
	/** Capitalized noun for accessible labels (e.g. "Agent"). */
	kindLabel: string;
	/** The actor id with a lifecycle mutation in flight, if any. */
	pendingId?: string | null;
	onAction: (item: T, action: AgentAction) => void;
	detailHref: (item: T) => string;
}

const DECISIONS: AgentAction[] = ['approve', 'deny'];

export function ApprovalQueue<T extends ActorRow>({
	items,
	kindLabel,
	pendingId,
	onAction,
	detailHref,
}: ApprovalQueueProps<T>) {
	if (items.length === 0) return null;

	return (
		<section className="space-y-2" aria-label={`Awaiting approval (${items.length})`}>
			{/* Section-title ladder: sentence-case font-heading, never the eyebrow
			    caption style (page-scaffold rule); the warning tint carries urgency. */}
			<h2 className="font-heading text-warning flex items-center gap-2 text-sm font-semibold">
				<span
					className="bg-warning h-1.5 w-1.5 animate-pulse rounded-full motion-reduce:animate-none"
					aria-hidden
				/>
				Awaiting approval ({items.length})
			</h2>
			<Card className="border-warning/30 bg-card divide-border/60 divide-y">
				{items.map((item) => {
					const rowPending = pendingId === item.id;
					return (
						<div
							key={item.id}
							className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3.5 py-2"
						>
							<AgentBadge id={item.id} name={item.name} kind={kindLabel} size="sm" />
							<div className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-2">
								<AppLink
									href={detailHref(item)}
									className="font-heading text-foreground hover:text-primary truncate text-sm font-semibold"
								>
									{item.name}
								</AppLink>
								<code className="text-muted-foreground truncate font-mono text-[11px]">
									{item.id}
								</code>
								<span
									className="text-muted-foreground text-[11px]"
									title={formatTimestamp(item.createdAt)}
								>
									· registered {timeAgo(item.createdAt)}
								</span>
							</div>
							<div className="flex items-center gap-2">
								{DECISIONS.map((action) => (
									<Button
										key={action}
										size="sm"
										variant={ACTION_VARIANT[action]}
										disabled={rowPending}
										loading={rowPending && action === 'approve'}
										onClick={() => onAction(item, action)}
										aria-label={`${ACTION_LABEL[action]} ${item.name}`}
									>
										{ACTION_LABEL[action]}
									</Button>
								))}
							</div>
						</div>
					);
				})}
			</Card>
		</section>
	);
}
