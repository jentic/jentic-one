import { AnimatePresence, motion } from 'framer-motion';
import { History, ScrollText } from 'lucide-react';
import { Badge, ErrorAlert, ActorLabel } from '@/shared/ui';
import { useToolkitAudit } from '@/modules/toolkits/api';
import { timeAgo } from '@/modules/toolkits/lib/time';
import type { ToolkitAuditEntry } from '@/modules/toolkits/api/types';

/**
 * Read-only, toolkit-scoped slice of the org-wide audit log. Surfaces the
 * toolkit-level events (create / update / suspend / restore) tagged
 * `target_type=toolkit`. Key- and binding-level sub-events live in the org-wide
 * Audit lens (Monitor module) — the `/audit` endpoint only filters by a single
 * `target_id`, so we don't duplicate those here. Requires `org:admin`; for
 * non-admins the repository maps 401/403 to an empty list and we render the
 * graceful "no entries" state.
 */
const rowMotion = {
	initial: { opacity: 0, y: -4 },
	animate: { opacity: 1, y: 0 },
	exit: { opacity: 0, y: -4 },
	transition: { duration: 0.16, ease: 'easeOut' as const },
};

function actionVariant(action: string): 'default' | 'success' | 'danger' {
	const a = action.toLowerCase();
	if (a.includes('delete') || a.includes('suspend') || a.includes('revoke')) return 'danger';
	if (a.includes('create')) return 'success';
	return 'default';
}

/** Fallback display when an entry has no resolvable actor id (system events). */
function fallbackActor(entry: ToolkitAuditEntry): string {
	if (entry.actor_type) return entry.actor_type;
	return 'system';
}

export interface ToolkitAuditPanelProps {
	toolkitId: string;
	poll?: boolean;
}

export function ToolkitAuditPanel({ toolkitId, poll = true }: ToolkitAuditPanelProps) {
	const { data: entries = [], isLoading, isError } = useToolkitAudit(toolkitId, { poll });

	return (
		<div className="bg-card border-border overflow-hidden rounded-xl border">
			<div className="border-border flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3.5 sm:px-5 sm:py-4">
				<h3 className="font-heading text-foreground flex items-center gap-2 font-semibold">
					<History className="h-4 w-4" aria-hidden="true" /> Activity
				</h3>
				<span className="text-muted-foreground text-xs">
					Toolkit-level events · admin only
				</span>
			</div>
			<div className="space-y-2 px-4 py-3.5 sm:px-5 sm:py-4">
				{isError && <ErrorAlert message="Failed to load the activity log." />}
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
							No recorded activity for this toolkit yet. The full audit trail lives in
							Monitor → Audit.
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
											actorType={entry.actor_type}
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
											? new Date(occurred).toLocaleString()
											: entry.occurred_at
									}
								>
									{Number.isFinite(occurred) ? timeAgo(occurred) : ''}
								</span>
							</motion.div>
						);
					})}
				</AnimatePresence>
			</div>
		</div>
	);
}
