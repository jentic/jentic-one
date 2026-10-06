/**
 * The call / job record for an open `trace_id` / `execution_id` / `job_id` —
 * what Everything, API calls and Jobs show in their detail pane. (The Audit
 * log renders its own entries; see AuditDetail.)
 */
import type { LogDetail } from '@/modules/monitor/lib/useLogDetail';
import type { DetailFrameContext } from '@/modules/monitor/components/LogDetailPane';
import { TraceDetail } from '@/modules/monitor/components/TraceDetail';
import { JobDetail } from '@/modules/monitor/components/JobDetail';

export function RecordDetail({ detail, frame }: { detail: LogDetail; frame: DetailFrameContext }) {
	if (detail.kind === 'job') return <JobDetail key={detail.id} jobId={detail.id} frame={frame} />;
	if (detail.kind === 'audit') return null;
	return (
		<TraceDetail
			key={`${detail.kind}:${detail.id}`}
			traceId={detail.kind === 'trace' ? detail.id : null}
			executionId={detail.kind === 'execution' ? detail.id : null}
			frame={frame}
		/>
	);
}
