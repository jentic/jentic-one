/**
 * Job detail sheet.
 *
 * Opened from the Jobs tab. Shows the job's fields, its resolved actor (from the
 * audit log via `job_id` — jobs carry no actor on the wire), a deep-link into
 * the Audit tab, and a Cancel action. Cancel is org:admin-only and only shown
 * while the job is in a non-terminal state.
 */
import { useId, useState } from 'react';
import { ArrowUpRight, User } from 'lucide-react';
import { SheetPrimitive, Button, ErrorAlert, LoadingState, AppLink, ActorLabel } from '@/shared/ui';
import {
	useJob,
	useCancelJob,
	useActorForJob,
	toJobStatus,
	isTerminalJobStatus,
} from '@/modules/monitor/api';
import { JobStatusPill } from '@/modules/monitor/components/StatusPill';
import { ConfirmDialog } from '@/modules/monitor/components/ConfirmDialog';
import { DetailRow, DetailSection } from '@/modules/monitor/components/Detail';
import { formatTimestamp } from '@/modules/monitor/lib/format';
import { monitorHref } from '@/modules/monitor/lib/links';
import { usePermission, ORG_ADMIN } from '@/modules/monitor/lib/usePermission';

interface JobDetailSheetProps {
	jobId: string | null;
	open: boolean;
	onClose: () => void;
}

export function JobDetailSheet({ jobId, open, onClose }: JobDetailSheetProps) {
	const query = useJob(jobId);
	const { actor } = useActorForJob(jobId);
	const cancel = useCancelJob();
	const isAdmin = usePermission(ORG_ADMIN);
	const headingId = useId();
	const [confirmOpen, setConfirmOpen] = useState(false);

	const job = query.data;
	const status = job ? toJobStatus(job.status) : null;
	const canCancel = isAdmin && status != null && !isTerminalJobStatus(status);

	const confirmCancel = () => {
		if (!job) return;
		cancel.mutate(job.job_id, {
			onSuccess: () => setConfirmOpen(false),
		});
	};

	return (
		<SheetPrimitive open={open} onClose={onClose} ariaLabelledBy={headingId}>
			<div className="flex h-full flex-col">
				<header className="border-border border-b px-5 py-4">
					<p className="text-muted-foreground text-xs font-medium tracking-wider uppercase">
						Job
					</p>
					<h2
						id={headingId}
						className="text-foreground mt-0.5 font-mono text-sm break-all"
					>
						{jobId ?? '—'}
					</h2>
				</header>

				<div className="flex-1 space-y-5 overflow-y-auto px-5 py-4">
					{query.isLoading ? (
						<LoadingState />
					) : query.isError || !job ? (
						<ErrorAlert
							message={
								query.error instanceof Error
									? query.error
									: 'Failed to load the job.'
							}
							onRetry={() => query.refetch()}
							retrying={query.isFetching}
						/>
					) : (
						<>
							<DetailSection title="Status">
								<div className="flex items-center gap-2">
									{status && <JobStatusPill status={status} />}
								</div>
							</DetailSection>

							<DetailSection title="Details">
								<DetailRow label="Kind" value={job.kind} mono />
								<DetailRow
									label="Created"
									value={formatTimestamp(job.created_at)}
								/>
								<DetailRow
									label="Updated"
									value={formatTimestamp(job.updated_at)}
								/>
								{job.execution_id && (
									<DetailRow
										label="Execution"
										value={
											<AppLink
												href={monitorHref({
													tab: 'executions',
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
								{job.error && (
									<p className="text-danger mt-2 text-xs">{job.error}</p>
								)}
							</DetailSection>

							<DetailSection
								title="Actor"
								action={
									jobId ? (
										<AppLink
											href={monitorHref({
												tab: 'audit',
												targetType: 'job',
												targetId: jobId,
											})}
											className="text-primary inline-flex items-center gap-1 text-xs font-medium hover:underline"
											aria-label={`View job ${jobId} in the audit log`}
										>
											View in audit
											<ArrowUpRight className="h-3 w-3" aria-hidden="true" />
										</AppLink>
									) : null
								}
							>
								<div className="flex items-center gap-2 text-sm">
									<User
										className="text-muted-foreground h-4 w-4"
										aria-hidden="true"
									/>
									{actor ? (
										actor.actorId ? (
											<ActorLabel
												actorId={actor.actorId}
												actorType={actor.actorType}
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
						</>
					)}
				</div>

				<footer className="border-border flex gap-2 border-t px-5 py-3">
					{canCancel && job && (
						<Button
							variant="danger"
							onClick={() => setConfirmOpen(true)}
							disabled={cancel.isPending}
							className="flex-1"
						>
							Cancel job
						</Button>
					)}
					<Button variant="outline" onClick={onClose} className="flex-1">
						Close
					</Button>
				</footer>
			</div>

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
		</SheetPrimitive>
	);
}
