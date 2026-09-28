/**
 * Audit record — the detail for one audit entry.
 *
 * The row says what happened in words; this is the evidence: who (and from
 * where — IP, client, session), what exactly changed (before → after), why
 * (the recorded reason), and every raw id with a copy button. The trace or
 * job the entry touched links back into API calls / Jobs.
 *
 * There's no single-entry endpoint, so the record renders from the row the
 * log already holds; an `audit_id` that isn't on the current page says so.
 */
import { ArrowUpRight, SearchX } from 'lucide-react';
import { ActorLabel, AppLink, EmptyState } from '@/shared/ui';
import type { AuditResponse } from '@/modules/monitor/api';
import { DetailRow, DetailSection } from '@/modules/monitor/components/Detail';
import {
	DetailFrame,
	IdRow,
	type DetailFrameContext,
} from '@/modules/monitor/components/LogDetailPane';
import { formatTimestamp } from '@/modules/monitor/lib/format';
import { originLabel } from '@/modules/monitor/lib/logVocabulary';
import { hasTrace, monitorHref } from '@/modules/monitor/lib/links';
import { auditChanges, auditSentence, auditTargetLabel } from '@/modules/monitor/lib/describeAudit';

export function AuditDetail({
	auditId,
	entry,
	frame,
}: {
	auditId: string;
	entry: AuditResponse | undefined;
	frame: DetailFrameContext;
}) {
	if (!entry) {
		return (
			<DetailFrame
				frame={frame}
				eyebrow="Audit entry"
				heading="Not on this page"
				id={auditId}
				idLabel="audit id"
			>
				<EmptyState
					icon={<SearchX className="h-8 w-8" />}
					title="Entry not loaded"
					description="This audit entry isn't in the page you're viewing — page through the log, or widen the time window, to find it."
				/>
			</DetailFrame>
		);
	}

	const changes = auditChanges(entry);

	return (
		<DetailFrame
			frame={frame}
			eyebrow="Audit entry"
			heading={auditSentence(entry)}
			id={entry.id}
			idLabel="audit id"
		>
			{entry.reason && (
				<p className="border-border bg-muted/30 rounded-md border px-3 py-2 text-sm">
					<span className="text-muted-foreground mr-1.5 text-xs">Reason</span>
					{entry.reason}
				</p>
			)}

			<DetailSection title="Who">
				<DetailRow
					label="Actor"
					value={
						entry.actor_id ? (
							<ActorLabel
								actorId={entry.actor_id}
								actorType={entry.actor_type}
								className="font-medium"
							/>
						) : (
							<span className="font-medium">{entry.actor_type}</span>
						)
					}
				/>
				<DetailRow label="When" value={formatTimestamp(entry.occurred_at)} />
				{entry.origin && <DetailRow label="Via" value={<>{originLabel(entry.origin)}</>} />}
				{entry.ip_address && <DetailRow label="IP address" value={entry.ip_address} mono />}
				{entry.user_agent && (
					<DetailRow
						label="Client"
						value={
							<span className="block truncate" title={entry.user_agent}>
								{entry.user_agent}
							</span>
						}
					/>
				)}
			</DetailSection>

			<DetailSection title="Target">
				<DetailRow label="Type" value={auditTargetLabel(entry.target_type)} />
				<IdRow label="Target id" value={entry.target_id} />
				{entry.target_parent_id && (
					<IdRow label="Parent id" value={entry.target_parent_id} />
				)}
			</DetailSection>

			{changes.length > 0 && (
				<DetailSection title={`Changes (${changes.length})`}>
					<ul className="border-border divide-border/60 divide-y rounded-lg border text-sm">
						{changes.map((c) => (
							<li key={c.field} className="px-3 py-2">
								<p className="text-muted-foreground font-mono text-xs">{c.field}</p>
								<p className="mt-0.5 flex flex-wrap items-baseline gap-x-2 break-all">
									<span className="text-muted-foreground line-through decoration-1">
										{c.before}
									</span>
									<span aria-hidden="true" className="text-muted-foreground">
										→
									</span>
									<span className="sr-only">changed to</span>
									<span className="text-foreground font-medium">{c.after}</span>
								</p>
							</li>
						))}
					</ul>
				</DetailSection>
			)}

			<DetailSection
				title="Ids"
				action={
					hasTrace(entry.trace_id) ? (
						<AppLink
							href={monitorHref({ show: 'calls', traceId: entry.trace_id })}
							className="text-primary inline-flex items-center gap-1 text-xs font-medium hover:underline"
							aria-label={`Open trace ${entry.trace_id} in API calls`}
						>
							Open trace
							<ArrowUpRight className="h-3 w-3" aria-hidden="true" />
						</AppLink>
					) : entry.job_id ? (
						<AppLink
							href={monitorHref({ show: 'jobs', jobId: entry.job_id })}
							className="text-primary inline-flex items-center gap-1 text-xs font-medium hover:underline"
							aria-label={`Open job ${entry.job_id} in Jobs`}
						>
							Open job
							<ArrowUpRight className="h-3 w-3" aria-hidden="true" />
						</AppLink>
					) : null
				}
			>
				<DetailRow label="Action" value={entry.action} mono />
				<IdRow label="Audit id" value={entry.id} />
				{entry.actor_id && <IdRow label="Actor id" value={entry.actor_id} />}
				{hasTrace(entry.trace_id) && <IdRow label="Trace id" value={entry.trace_id} />}
				{entry.job_id && <IdRow label="Job id" value={entry.job_id} />}
				{entry.request_id && <IdRow label="Request id" value={entry.request_id} />}
				{entry.actor_session_id && (
					<IdRow label="Session id" value={entry.actor_session_id} />
				)}
			</DetailSection>
		</DetailFrame>
	);
}
