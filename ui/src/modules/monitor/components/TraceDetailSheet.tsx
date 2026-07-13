/**
 * Trace detail sheet.
 *
 * Opened from the Executions tab. Shows every execution sharing a `trace_id`
 * (a trace can fan out into multiple API calls), plus the resolved actor for
 * the trace — surfaced from the audit log via `actor_id`/`actor_type`, since
 * execution payloads carry no actor on the wire (STATUS.md decision). Offers a
 * deep-link into the Audit tab scoped to this trace.
 */
import { useMemo, useId } from 'react';
import { ArrowUpRight, User } from 'lucide-react';
import {
	SheetPrimitive,
	Button,
	ErrorAlert,
	LoadingState,
	StatusBadge,
	AppLink,
	ActorLabel,
} from '@/shared/ui';
import {
	useExecutions,
	useExecution,
	useActorForTrace,
	toExecutionStatus,
	type ExecutionResponse,
} from '@/modules/monitor/api';
import { ExecutionStatusPill } from '@/modules/monitor/components/StatusPill';
import { DetailRow, DetailSection } from '@/modules/monitor/components/Detail';
import { formatDuration, formatTimestamp } from '@/modules/monitor/lib/format';
import { monitorHref, hasTrace } from '@/modules/monitor/lib/links';

interface TraceDetailSheetProps {
	traceId: string | null;
	/**
	 * Fallback identity when the row has no usable trace (the backend stores
	 * `trace_id="unknown"` for header-less runs). Lets the sheet show that one
	 * execution instead of grouping every "unknown" execution together.
	 */
	executionId?: string | null;
	open: boolean;
	onClose: () => void;
}

export function TraceDetailSheet({
	traceId,
	executionId = null,
	open,
	onClose,
}: TraceDetailSheetProps) {
	// A real trace groups every execution that shares it; an unusable trace
	// ("unknown"/empty) falls back to the single execution we were opened with.
	const traceable = hasTrace(traceId);
	const listQuery = useExecutions(traceable ? { traceId } : {});
	const singleQuery = useExecution(!traceable && executionId ? executionId : null);
	const { actor } = useActorForTrace(traceable ? traceId : null);
	const headingId = useId();

	const query = traceable ? listQuery : singleQuery;

	const executions: ExecutionResponse[] = useMemo(() => {
		if (traceable) {
			return (listQuery.data?.data ?? []).filter((e) => e.trace_id === traceId);
		}
		return singleQuery.data ? [singleQuery.data] : [];
	}, [traceable, listQuery.data, singleQuery.data, traceId]);

	const headingLabel = traceable ? traceId : (executionId ?? '—');

	return (
		<SheetPrimitive open={open} onClose={onClose} ariaLabelledBy={headingId}>
			<div className="flex h-full flex-col">
				<header className="border-border border-b px-5 py-4">
					<p className="text-muted-foreground text-xs font-medium tracking-wider uppercase">
						{traceable ? 'Trace' : 'Execution'}
					</p>
					<h2
						id={headingId}
						className="text-foreground mt-0.5 font-mono text-sm break-all"
					>
						{headingLabel ?? '—'}
					</h2>
				</header>

				<div className="flex-1 space-y-5 overflow-y-auto px-5 py-4">
					{query.isLoading ? (
						<LoadingState />
					) : query.isError ? (
						<ErrorAlert
							message={
								query.error instanceof Error
									? query.error
									: 'Failed to load the trace.'
							}
							onRetry={() => query.refetch()}
							retrying={query.isFetching}
						/>
					) : (
						<>
							<DetailSection title="Actor">
								<div className="flex items-center gap-2 text-sm">
									<User
										className="text-muted-foreground h-4 w-4"
										aria-hidden="true"
									/>
									{!traceable ? (
										<span className="text-muted-foreground">
											No trace recorded for this execution, so its actor
											can&rsquo;t be resolved from the audit log.
										</span>
									) : actor ? (
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
											No actor recorded in the audit log for this trace.
										</span>
									)}
								</div>
							</DetailSection>

							<DetailSection
								title={`Executions (${executions.length})`}
								action={
									traceable && traceId ? (
										<AppLink
											href={monitorHref({ tab: 'audit', traceId })}
											className="text-primary inline-flex items-center gap-1 text-xs font-medium hover:underline"
											aria-label={`View trace ${traceId} in the audit log`}
										>
											View in audit
											<ArrowUpRight className="h-3 w-3" aria-hidden="true" />
										</AppLink>
									) : null
								}
							>
								<div className="space-y-3">
									{executions.map((exec) => (
										<div
											key={exec.execution_id}
											className="border-border rounded-lg border p-3"
										>
											<div className="flex items-center justify-between gap-2">
												<ExecutionStatusPill
													status={toExecutionStatus(exec.status)}
												/>
												<StatusBadge status={exec.http_status} />
											</div>
											<DetailRow
												label="Operation"
												value={exec.operation_id ?? '—'}
												mono
											/>
											<DetailRow
												label="API"
												value={exec.api?.host ?? exec.toolkit_id}
											/>
											<DetailRow
												label="Duration"
												value={formatDuration(exec.duration_ms)}
											/>
											<DetailRow
												label="Started"
												value={formatTimestamp(exec.started_at)}
											/>
											{exec.error && (
												<p className="text-danger mt-2 text-xs">
													{exec.error}
												</p>
											)}
										</div>
									))}
								</div>
							</DetailSection>
						</>
					)}
				</div>

				<footer className="border-border border-t px-5 py-3">
					<Button variant="outline" onClick={onClose} className="w-full">
						Close
					</Button>
				</footer>
			</div>
		</SheetPrimitive>
	);
}
