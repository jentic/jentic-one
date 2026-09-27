/**
 * Trace / execution record — the detail for an API call.
 *
 * Opened by `trace_id` (every execution sharing it — a trace can fan out into
 * several calls) or by `execution_id` for a header-less run whose trace is the
 * placeholder "unknown". Shows the outcome up top, then who made the call,
 * what it hit, and the raw ids with copy buttons. "View in audit" (org:admin)
 * opens the audit log scoped to this trace.
 */
import { useMemo } from 'react';
import { ArrowUpRight } from 'lucide-react';
import {
	ActorLabel,
	AppLink,
	ErrorAlert,
	LoadingState,
	StatusBadge,
	VendorIcon,
} from '@/shared/ui';
import {
	useActorForTrace,
	useExecution,
	useExecutions,
	toExecutionStatus,
	type ExecutionResponse,
} from '@/modules/monitor/api';
import { DetailRow, DetailSection } from '@/modules/monitor/components/Detail';
import {
	DetailFrame,
	IdRow,
	type DetailFrameContext,
} from '@/modules/monitor/components/LogDetailPane';
import { StatusGlyph } from '@/modules/monitor/components/LogList';
import { ExecutionStatusPill } from '@/modules/monitor/components/StatusPill';
import { formatDuration, formatTimestamp } from '@/modules/monitor/lib/format';
import { hasTrace, monitorHref } from '@/modules/monitor/lib/links';
import { originLabel, EXECUTION_LABEL, EXECUTION_TONE } from '@/modules/monitor/lib/logVocabulary';
import { ORG_ADMIN, usePermission } from '@/modules/monitor/lib/usePermission';

function apiName(exec: ExecutionResponse): string {
	return exec.api?.name ?? exec.api?.host ?? 'Unknown API';
}

function credentialName(exec: ExecutionResponse): string {
	// Historical rows predate direct bindings and carry only the legacy
	// toolkit attribution — show it read-only.
	return (
		exec.credential_name ?? exec.credential_id ?? exec.toolkit_name ?? exec.toolkit_id ?? '—'
	);
}

export function TraceDetail({
	traceId,
	executionId,
	frame,
}: {
	traceId: string | null;
	executionId: string | null;
	frame: DetailFrameContext;
}) {
	const isAdmin = usePermission(ORG_ADMIN);
	// Opened by execution id, the record must still find that execution's
	// trace: fetch it whenever we have its id and read the trace off it.
	const singleQuery = useExecution(executionId);
	const single = singleQuery.data;
	const effectiveTraceId = hasTrace(traceId)
		? traceId
		: hasTrace(single?.trace_id)
			? (single?.trace_id ?? null)
			: null;
	const traceable = effectiveTraceId != null;
	// A real trace groups every execution that shares it; never list unfiltered.
	const listQuery = useExecutions(traceable ? { traceId: effectiveTraceId } : {}, {
		enabled: traceable,
	});
	const traceActor = useActorForTrace(effectiveTraceId, { canReadAudit: isAdmin }).actor;
	const actor =
		single && (single.actor_id || single.actor_type)
			? { actorId: single.actor_id || null, actorType: single.actor_type }
			: traceActor;

	const query = traceable ? listQuery : singleQuery;
	const isResolving = executionId != null && !hasTrace(traceId) && singleQuery.isLoading;

	const executions: ExecutionResponse[] = useMemo(() => {
		if (traceable) {
			const rows = (listQuery.data?.data ?? []).filter(
				(e) => e.trace_id === effectiveTraceId,
			);
			// Keep the execution we were opened with even if the trace page
			// hasn't caught up with it.
			if (single && !rows.some((e) => e.execution_id === single.execution_id)) {
				return [single, ...rows];
			}
			return rows;
		}
		return single ? [single] : [];
	}, [traceable, listQuery.data, single, effectiveTraceId]);

	const first = executions[0];
	const heading =
		executions.length > 1
			? `${executions.length} calls in one trace`
			: (first?.operation_id ?? (traceable ? 'Trace' : 'API call'));
	const id = traceable ? effectiveTraceId : (executionId ?? '—');

	return (
		<DetailFrame
			frame={frame}
			eyebrow={traceable ? 'Trace' : 'Execution'}
			heading={heading}
			id={id}
			idLabel={traceable ? 'trace id' : 'execution id'}
		>
			{isResolving || query.isLoading ? (
				<LoadingState />
			) : query.isError ? (
				<ErrorAlert
					message={
						query.error instanceof Error ? query.error : 'Failed to load the trace.'
					}
					onRetry={() => query.refetch()}
					retrying={query.isFetching}
				/>
			) : (
				<>
					<DetailSection title="Who">
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
									{traceable
										? 'No actor recorded for this trace.'
										: 'No actor recorded for this execution.'}
								</span>
							)}
						</div>
					</DetailSection>

					<DetailSection
						title={executions.length > 1 ? `Calls (${executions.length})` : 'Call'}
						action={
							traceable && isAdmin ? (
								<AppLink
									href={monitorHref({ show: 'audit', traceId: effectiveTraceId })}
									className="text-primary inline-flex items-center gap-1 text-xs font-medium hover:underline"
									aria-label={`View trace ${effectiveTraceId} in the audit log`}
								>
									View in audit
									<ArrowUpRight className="h-3 w-3" aria-hidden="true" />
								</AppLink>
							) : null
						}
					>
						<ol className="space-y-3">
							{executions.map((exec) => (
								<ExecutionCard key={exec.execution_id} exec={exec} />
							))}
						</ol>
					</DetailSection>
				</>
			)}
		</DetailFrame>
	);
}

function ExecutionCard({ exec }: { exec: ExecutionResponse }) {
	const status = toExecutionStatus(exec.status);
	return (
		<li className="border-border/70 bg-muted/20 rounded-lg border p-3">
			<div className="flex items-start gap-2">
				<StatusGlyph tone={EXECUTION_TONE[status]} label={EXECUTION_LABEL[status]} />
				<p className="text-foreground min-w-0 flex-1 font-mono text-[13px] break-all">
					{exec.operation_id ?? '—'}
				</p>
				<StatusBadge status={exec.http_status} />
			</div>
			{exec.error && (
				<p className="bg-danger/[0.06] text-danger mt-2 rounded-md px-2 py-1.5 text-xs">
					{exec.error}
				</p>
			)}
			<div className="mt-2">
				<DetailRow label="Outcome" value={<ExecutionStatusPill status={status} />} />
				<DetailRow
					label="API"
					value={
						<span className="inline-flex items-center gap-1.5">
							<VendorIcon
								name={apiName(exec)}
								vendor={exec.api?.vendor ?? undefined}
								size="sm"
								className="h-5 w-5 rounded text-[7px]"
							/>
							{apiName(exec)}
							{exec.api?.host && exec.api.host !== apiName(exec) && (
								<span className="text-muted-foreground">· {exec.api.host}</span>
							)}
						</span>
					}
				/>
				<DetailRow label="Credential" value={credentialName(exec)} />
				{exec.origin && <DetailRow label="Via" value={<>{originLabel(exec.origin)}</>} />}
				<DetailRow label="Duration" value={formatDuration(exec.duration_ms)} />
				<DetailRow label="Started" value={formatTimestamp(exec.started_at)} />
				<IdRow label="Execution id" value={exec.execution_id} />
			</div>
		</li>
	);
}
