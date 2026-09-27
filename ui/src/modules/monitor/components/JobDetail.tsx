/**
 * Job record — the detail for one async job.
 *
 * Shows the job's state and timing, the execution it produced (if any), its
 * resolved actor (from the audit log via `job_id` — jobs carry no actor on
 * the wire) and a "View in audit" link. Cancel is org:admin-only and offered
 * only while the job is still queued or running.
 */
import { useState } from 'react';
import { ArrowUpRight } from 'lucide-react';
import { ActorLabel, AppLink, Button, ErrorAlert, LoadingState } from '@/shared/ui';
import {
	isTerminalJobStatus,
	toJobStatus,
	useActorForJob,
	useCancelJob,
	useJob,
} from '@/modules/monitor/api';
import { ConfirmDialog } from '@/modules/monitor/components/ConfirmDialog';
import { DetailRow, DetailSection } from '@/modules/monitor/components/Detail';
import {
	DetailFrame,
	IdRow,
	type DetailFrameContext,
} from '@/modules/monitor/components/LogDetailPane';
import { JobStatusPill } from '@/modules/monitor/components/StatusPill';
import { formatTimestamp } from '@/modules/monitor/lib/format';
import { monitorHref } from '@/modules/monitor/lib/links';
import { formatSpan, jobKindLabel, jobSentence } from '@/modules/monitor/lib/logVocabulary';
import { ORG_ADMIN, usePermission } from '@/modules/monitor/lib/usePermission';

export function JobDetail({ jobId, frame }: { jobId: string; frame: DetailFrameContext }) {
	const query = useJob(jobId);
	const isAdmin = usePermission(ORG_ADMIN);
	const { actor } = useActorForJob(jobId, { canReadAudit: isAdmin });
	const cancel = useCancelJob();
	const [confirmOpen, setConfirmOpen] = useState(false);

	const job = query.data;
	const status = job ? toJobStatus(job.status) : null;
	const canCancel = isAdmin && status != null && !isTerminalJobStatus(status);
	const span = job ? formatSpan(job.created_at, job.updated_at) : null;

	const confirmCancel = () => {
		if (!job) return;
		cancel.mutate(job.job_id, { onSuccess: () => setConfirmOpen(false) });
	};

	return (
		<DetailFrame
			frame={frame}
			eyebrow="Job"
			heading={job && status ? jobSentence(job.kind, status) : 'Job'}
			id={jobId}
			idLabel="job id"
			actions={
				canCancel && job ? (
					<Button
						variant="danger"
						size={frame.mode === 'pane' ? 'sm' : undefined}
						onClick={() => setConfirmOpen(true)}
						disabled={cancel.isPending}
						className={frame.mode === 'sheet' ? 'flex-1' : undefined}
					>
						Cancel job
					</Button>
				) : undefined
			}
		>
			{query.isLoading ? (
				<LoadingState />
			) : query.isError || !job || !status ? (
				<ErrorAlert
					message={query.error instanceof Error ? query.error : 'Failed to load the job.'}
					onRetry={() => query.refetch()}
					retrying={query.isFetching}
				/>
			) : (
				<>
					{job.error && (
						<p className="bg-danger/[0.06] text-danger rounded-md px-3 py-2 text-sm">
							{job.error}
						</p>
					)}

					<DetailSection title="Status">
						<DetailRow label="State" value={<JobStatusPill status={status} />} />
						<DetailRow label="Kind" value={jobKindLabel(job.kind)} />
						<DetailRow label="Queued" value={formatTimestamp(job.created_at)} />
						<DetailRow label="Last update" value={formatTimestamp(job.updated_at)} />
						{span && (
							<DetailRow
								label={isTerminalJobStatus(status) ? 'Took' : 'Running for'}
								value={span}
							/>
						)}
					</DetailSection>

					<DetailSection
						title="Who"
						action={
							<AppLink
								href={monitorHref({
									show: 'audit',
									targetType: 'job',
									targetId: jobId,
								})}
								className="text-primary inline-flex items-center gap-1 text-xs font-medium hover:underline"
								aria-label={`View job ${jobId} in the audit log`}
							>
								View in audit
								<ArrowUpRight className="h-3 w-3" aria-hidden="true" />
							</AppLink>
						}
					>
						<div className="text-sm">
							{actor ? (
								actor.actorId ? (
									<ActorLabel
										actorId={actor.actorId}
										actorType={actor.actorType}
										className="font-medium"
									/>
								) : (
									<span className="font-medium">{actor.actorType}</span>
								)
							) : (
								<span className="text-muted-foreground">
									No actor recorded in the audit log for this job.
								</span>
							)}
						</div>
					</DetailSection>

					<DetailSection title="Ids">
						<IdRow label="Job id" value={job.job_id} />
						<DetailRow label="Raw kind" value={job.kind} mono />
						{job.execution_id && (
							<DetailRow
								label="Execution"
								value={
									<AppLink
										href={monitorHref({
											show: 'calls',
											executionId: job.execution_id,
										})}
										className="text-primary font-mono text-xs hover:underline"
										aria-label={`Open execution ${job.execution_id}`}
									>
										{job.execution_id}
									</AppLink>
								}
							/>
						)}
					</DetailSection>
				</>
			)}

			<ConfirmDialog
				open={confirmOpen}
				title="Cancel this job?"
				body={
					<>
						Cancelling stops <span className="font-mono">{job?.kind}</span> job{' '}
						<span className="font-mono">{jobId}</span>. This can't be undone — a
						cancelled job won't resume.
					</>
				}
				confirmLabel="Cancel job"
				onConfirm={confirmCancel}
				onClose={() => setConfirmOpen(false)}
				pending={cancel.isPending}
			/>
		</DetailFrame>
	);
}
