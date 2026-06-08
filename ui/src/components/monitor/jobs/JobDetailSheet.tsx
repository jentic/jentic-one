import { type JSX } from 'react';
import {
	X,
	AlertCircle,
	Clock,
	Zap,
	Layers,
	Hash,
	ArrowRight,
	User,
	Briefcase,
	Workflow,
} from 'lucide-react';
import { JobStatusPill } from './JobStatusPill';
import { formatDuration } from '@/components/monitor/shared/format';
import type { JobLogEntry } from '@/components/monitor/types';
import { cn } from '@/lib/utils';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { SheetPrimitive } from '@/components/ui/SheetPrimitive';

interface JobDetailSheetProps {
	job: JobLogEntry | null;
	isOpen: boolean;
	isLoading: boolean;
	isCancelling: boolean;
	canCancel: boolean;
	onClose: () => void;
	onCancel: (jobId: string) => void;
	/** When set, the linked-trace surface becomes a clickable "open trace" button. */
	onOpenTrace?: (traceId: string) => void;
	side?: 'left' | 'right';
}

function formatTimestamp(dateString: string): string {
	return new Date(dateString).toLocaleString('en-US', {
		month: 'short',
		day: 'numeric',
		hour: '2-digit',
		minute: '2-digit',
	});
}

function MetricPill({
	icon,
	label,
	value,
	mono,
}: {
	icon: JSX.Element;
	label: string;
	value: string;
	mono?: boolean;
}): JSX.Element {
	return (
		<div className="border-border bg-muted/30 flex items-center gap-2.5 rounded-lg border px-3 py-2">
			<div className="bg-muted flex h-7 w-7 shrink-0 items-center justify-center rounded-md">
				{icon}
			</div>
			<div className="min-w-0">
				<p className="text-muted-foreground text-[10px] tracking-wider uppercase">
					{label}
				</p>
				<p
					className={cn(
						'text-foreground truncate text-sm font-medium',
						mono && 'font-mono',
					)}
				>
					{value}
				</p>
			</div>
		</div>
	);
}

export function JobDetailSheet({
	job,
	isOpen,
	isLoading,
	isCancelling,
	canCancel,
	onClose,
	onCancel,
	onOpenTrace,
	side = 'right',
}: JobDetailSheetProps): JSX.Element {
	const KindIcon = job?.kind === 'workflow' ? Workflow : Briefcase;

	return (
		<SheetPrimitive
			open={isOpen}
			onClose={onClose}
			side={side}
			ariaLabel="Job detail"
			className="w-full sm:w-[480px] sm:max-w-[90vw]"
		>
			<div className="flex h-full flex-col">
				<div className="border-border flex items-center justify-between border-b px-4 py-3">
					<h2 className="text-foreground text-sm font-semibold">Job Detail</h2>
					<button
						type="button"
						onClick={onClose}
						className="text-muted-foreground hover:bg-muted hover:text-foreground cursor-pointer rounded-md p-1.5 transition-colors"
						aria-label="Close"
					>
						<X className="h-4 w-4" />
					</button>
				</div>

				<div className="flex-1 overflow-y-auto">
					{isLoading ? (
						<div className="flex items-center justify-center py-16">
							<LoadingSpinner size="lg" />
						</div>
					) : job ? (
						<div className="space-y-5 p-4">
							<div className="flex items-start gap-3.5">
								<div className="bg-muted flex h-10 w-10 shrink-0 items-center justify-center rounded-xl">
									<KindIcon className="text-muted-foreground h-5 w-5" />
								</div>
								<div className="min-w-0 flex-1">
									<h3 className="text-foreground truncate text-base font-semibold">
										{job.capability ?? 'Unknown capability'}
									</h3>
									<p className="text-muted-foreground mt-0.5 truncate font-mono text-xs">
										{job.jobId}
									</p>
								</div>
								<JobStatusPill status={job.status} />
							</div>

							{job.errorMessage && (
								<div className="border-accent-red/20 bg-accent-red/5 flex items-start gap-2.5 rounded-lg border p-3">
									<AlertCircle className="text-accent-red mt-0.5 h-4 w-4 shrink-0" />
									<p className="text-accent-red text-sm leading-relaxed">
										{job.errorMessage}
									</p>
								</div>
							)}

							<div className="grid grid-cols-2 gap-2">
								<MetricPill
									icon={<Clock className="text-muted-foreground h-3.5 w-3.5" />}
									label="Started"
									value={formatTimestamp(job.createdAt)}
								/>
								{job.durationMs !== undefined && (
									<MetricPill
										icon={<Zap className="text-muted-foreground h-3.5 w-3.5" />}
										label="Duration"
										value={formatDuration(job.durationMs)}
										mono
									/>
								)}
								<MetricPill
									icon={<Layers className="text-muted-foreground h-3.5 w-3.5" />}
									label="Toolkit"
									value={job.toolkitName ?? '—'}
								/>
								<MetricPill
									icon={<User className="text-muted-foreground h-3.5 w-3.5" />}
									label="Agent"
									value={job.agentName ?? '—'}
								/>
								{job.httpStatus !== null && (
									<MetricPill
										icon={
											<Hash className="text-muted-foreground h-3.5 w-3.5" />
										}
										label="HTTP"
										value={String(job.httpStatus)}
										mono
									/>
								)}
							</div>

							{(job.traceId || job.parentTraceId) && (
								<div className="border-border bg-muted/20 space-y-1.5 rounded-lg border px-3 py-2">
									<p className="text-muted-foreground text-[10px] tracking-wider uppercase">
										Linked Context
									</p>
									{job.parentTraceId &&
										(() => {
											const parentTraceId = job.parentTraceId;
											return (
												<div className="flex items-center gap-2">
													<span className="text-muted-foreground text-[10px] uppercase">
														Parent
													</span>
													{onOpenTrace ? (
														<button
															type="button"
															onClick={() =>
																onOpenTrace(parentTraceId)
															}
															className="text-foreground hover:text-accent-blue truncate font-mono text-xs underline-offset-2 hover:underline"
														>
															{parentTraceId}
														</button>
													) : (
														<span className="text-muted-foreground truncate font-mono text-xs">
															{parentTraceId}
														</span>
													)}
												</div>
											);
										})()}
									{job.traceId &&
										(() => {
											const traceId = job.traceId;
											return (
												<div className="flex items-center gap-2">
													<span className="text-muted-foreground text-[10px] uppercase">
														Trace
													</span>
													{onOpenTrace ? (
														<button
															type="button"
															onClick={() => onOpenTrace(traceId)}
															className="text-foreground hover:text-accent-blue truncate font-mono text-xs underline-offset-2 hover:underline"
														>
															{traceId}
														</button>
													) : (
														<span className="text-muted-foreground truncate font-mono text-xs">
															{traceId}
														</span>
													)}
												</div>
											);
										})()}
								</div>
							)}

							{job.upstreamJobUrl && (
								<div className="border-border bg-muted/20 rounded-lg border px-3 py-2">
									<p className="text-muted-foreground text-[10px] tracking-wider uppercase">
										Upstream Job URL
									</p>
									<p className="text-muted-foreground mt-0.5 truncate font-mono text-xs">
										{job.upstreamJobUrl}
									</p>
								</div>
							)}

							{canCancel && (
								<div className="border-border border-t pt-4">
									<button
										type="button"
										onClick={() => onCancel(job.jobId)}
										disabled={isCancelling}
										className={cn(
											'border-accent-red/30 text-accent-red hover:bg-accent-red/10 w-full rounded-lg border px-3 py-2 text-sm font-medium transition-colors',
											'disabled:cursor-not-allowed disabled:opacity-50',
										)}
									>
										{isCancelling ? 'Cancelling…' : 'Cancel job'}
									</button>
									<p className="text-muted-foreground mt-1.5 text-[10px] leading-relaxed">
										Best-effort: cancellation fires at the next async
										checkpoint. An in-flight upstream HTTP call will complete
										before the job stops.
									</p>
								</div>
							)}
						</div>
					) : (
						<div className="flex flex-col items-center justify-center py-16 text-center">
							<div className="bg-muted mb-2 rounded-full p-3">
								<ArrowRight className="text-muted-foreground h-5 w-5" />
							</div>
							<p className="text-muted-foreground text-sm">
								Select a job to view details
							</p>
						</div>
					)}
				</div>
			</div>
		</SheetPrimitive>
	);
}
