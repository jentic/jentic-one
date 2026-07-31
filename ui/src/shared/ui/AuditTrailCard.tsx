import type { ReactNode } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { History, ScrollText } from 'lucide-react';
import { ActorLabel } from '@/shared/ui/ActorLabel';
import { Badge } from '@/shared/ui/Badge';
import { ErrorAlert } from '@/shared/ui/ErrorAlert';
import { DetailSection, EmptyRow } from '@/shared/ui/DetailSection';
import { formatTimestamp, timeAgo } from '@/shared/lib/utils';

/**
 * AuditTrailCard — the "Recent changes" card shared by the detail consoles
 * (toolkit, agent, service account): a read-only, entity-scoped slice of the
 * org-wide audit log. One component so "Recent changes" reads identically
 * everywhere; callers own the data fetch (per-module hooks) and map their
 * wire rows into {@link AuditTrailEntry}.
 */

export interface AuditTrailEntry {
	id: string;
	/** The audit verb, rendered as a badge ("toolkit.suspend", "agent.approve"). */
	action: string;
	/** The PERFORMING principal (resolved via ActorLabel); null for system events. */
	actorId?: string | null;
	actorType?: string | null;
	/** Optional operator-supplied reason, rendered after the actor. */
	reason?: string | null;
	/** ISO timestamp of the event. */
	occurredAt: string;
}

const rowMotion = {
	initial: { opacity: 0, y: -4 },
	animate: { opacity: 1, y: 0 },
	exit: { opacity: 0, y: -4 },
	transition: { duration: 0.16, ease: 'easeOut' as const },
};

/**
 * Audit verb → badge tint. The union of the consoles' vocabularies so a verb
 * reads the same wherever it appears; unknown verbs fall through to neutral.
 */
function actionVariant(action: string): 'default' | 'success' | 'danger' {
	const a = action.toLowerCase();
	if (
		a.includes('delete') ||
		a.includes('deny') ||
		a.includes('disable') ||
		a.includes('archive') ||
		a.includes('suspend') ||
		a.includes('revoke')
	) {
		return 'danger';
	}
	if (a.includes('create') || a.includes('register') || a.includes('approve')) return 'success';
	return 'default';
}

export interface AuditTrailCardProps {
	entries: AuditTrailEntry[];
	isLoading?: boolean;
	isError?: boolean;
	/** Quiet right-slot caption, e.g. "Toolkit-level events · admin only". */
	caption?: string;
	/** Dashed empty-state copy when there are no entries. */
	emptyMessage: ReactNode;
	errorMessage?: string;
}

export function AuditTrailCard({
	entries,
	isLoading = false,
	isError = false,
	caption,
	emptyMessage,
	errorMessage = 'Failed to load the audit log.',
}: AuditTrailCardProps) {
	return (
		<DetailSection
			title="Recent changes"
			icon={<History className="h-4 w-4" />}
			trailing={
				caption ? <span className="text-muted-foreground text-xs">{caption}</span> : null
			}
		>
			{isError && <ErrorAlert message={errorMessage} />}
			{!isError && isLoading && (
				<>
					<div className="bg-muted h-10 animate-pulse rounded-lg" />
					<div className="bg-muted h-10 animate-pulse rounded-lg" />
				</>
			)}
			{!isError && !isLoading && entries.length === 0 && (
				<EmptyRow icon={<ScrollText />}>{emptyMessage}</EmptyRow>
			)}
			{!isError && !isLoading && (
				<AnimatePresence initial={false}>
					{entries.map((entry) => {
						const occurred = Date.parse(entry.occurredAt);
						return (
							<motion.div
								key={entry.id}
								{...rowMotion}
								className="bg-muted/30 border-border/60 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border px-3 py-2"
							>
								<Badge variant={actionVariant(entry.action)}>{entry.action}</Badge>
								<span className="text-foreground min-w-0 flex-1 truncate text-sm">
									{entry.actorId ? (
										<ActorLabel
											actorId={entry.actorId}
											actorType={entry.actorType ?? undefined}
										/>
									) : (
										(entry.actorType ?? 'system')
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
											? formatTimestamp(entry.occurredAt)
											: entry.occurredAt
									}
								>
									{Number.isFinite(occurred) ? timeAgo(entry.occurredAt) : ''}
								</span>
							</motion.div>
						);
					})}
				</AnimatePresence>
			)}
		</DetailSection>
	);
}
