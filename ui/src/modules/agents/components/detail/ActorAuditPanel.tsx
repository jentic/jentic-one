/**
 * ActorAuditPanel — the "Recent changes" card on the agent / service-account
 * detail Overview tab. A read-only, actor-scoped slice of the org-wide audit
 * log: the lifecycle trail recorded against this actor as the TARGET
 * (register, approve/deny, disable/enable, key rotation, toolkit grant/
 * revoke). Deliberately the same visual grammar as the toolkit console's
 * `ToolkitAuditPanel` so "Recent changes" reads identically across consoles.
 *
 * Requires `org:admin` — the repository maps 401/403 to an empty list, so
 * non-admins see the graceful "no entries" state rather than an error.
 */
import { AnimatePresence, motion } from 'framer-motion';
import { History, ScrollText } from 'lucide-react';
import { ActorLabel, Badge, ErrorAlert } from '@/shared/ui';
import { formatTimestamp, timeAgo } from '@/shared/lib/utils';
import { useActorAudit, type ActorAuditEntry } from '@/modules/agents/api';

const rowMotion = {
	initial: { opacity: 0, y: -4 },
	animate: { opacity: 1, y: 0 },
	exit: { opacity: 0, y: -4 },
	transition: { duration: 0.16, ease: 'easeOut' as const },
};

function actionVariant(action: string): 'default' | 'success' | 'danger' {
	const a = action.toLowerCase();
	if (
		a.includes('delete') ||
		a.includes('deny') ||
		a.includes('disable') ||
		a.includes('archive') ||
		a.includes('revoke')
	) {
		return 'danger';
	}
	if (a.includes('create') || a.includes('register') || a.includes('approve')) return 'success';
	return 'default';
}

/** Fallback display when an entry has no resolvable actor id (system events). */
function fallbackActor(entry: ActorAuditEntry): string {
	return entry.actor_type ?? 'system';
}

export interface ActorAuditPanelProps {
	actorKind: 'agent' | 'service-account';
	actorId: string;
}

export function ActorAuditPanel({ actorKind, actorId }: ActorAuditPanelProps) {
	const { data: entries = [], isLoading, isError } = useActorAudit(actorKind, actorId);

	return (
		<div className="bg-card border-border overflow-hidden rounded-xl border">
			<div className="border-border flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3.5 sm:px-5 sm:py-4">
				<h3 className="font-heading text-foreground flex items-center gap-2.5 font-semibold">
					<span
						aria-hidden="true"
						className="bg-muted text-muted-foreground ring-border flex h-7 w-7 shrink-0 items-center justify-center rounded-lg ring-1"
					>
						<History className="h-4 w-4" />
					</span>
					Recent changes
				</h3>
				<span className="text-muted-foreground text-xs">Lifecycle events · admin only</span>
			</div>
			<div className="space-y-2 px-4 py-3.5 sm:px-5 sm:py-4">
				{isError && <ErrorAlert message="Failed to load the audit log." />}
				{!isError && isLoading && (
					<>
						<div className="bg-muted h-10 animate-pulse rounded-lg" />
						<div className="bg-muted h-10 animate-pulse rounded-lg" />
					</>
				)}
				{!isError && !isLoading && entries.length === 0 && (
					<div className="border-border/50 rounded-lg border border-dashed px-5 py-6 text-center">
						<ScrollText className="text-muted-foreground/50 mx-auto h-6 w-6" />
						<p className="text-muted-foreground mt-2 text-sm">
							No recorded changes for this{' '}
							{actorKind === 'agent' ? 'agent' : 'service account'} yet. The full
							audit trail lives in Monitor → Audit.
						</p>
					</div>
				)}
				<AnimatePresence initial={false}>
					{entries.map((entry) => {
						const occurred = Date.parse(entry.occurred_at);
						return (
							<motion.div
								key={entry.id}
								{...rowMotion}
								className="bg-muted/30 border-border/60 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border px-3 py-2"
							>
								<Badge variant={actionVariant(entry.action)}>{entry.action}</Badge>
								<span className="text-foreground min-w-0 flex-1 truncate text-sm">
									{entry.actor_id ? (
										<ActorLabel
											actorId={entry.actor_id}
											actorType={entry.actor_type ?? undefined}
										/>
									) : (
										fallbackActor(entry)
									)}
									{entry.reason ? (
										<span className="text-muted-foreground">
											{' '}
											— {entry.reason}
										</span>
									) : null}
								</span>
								<span
									className="text-muted-foreground shrink-0 text-xs"
									title={
										Number.isFinite(occurred)
											? formatTimestamp(entry.occurred_at)
											: entry.occurred_at
									}
								>
									{Number.isFinite(occurred) ? timeAgo(entry.occurred_at) : ''}
								</span>
							</motion.div>
						);
					})}
				</AnimatePresence>
			</div>
		</div>
	);
}
