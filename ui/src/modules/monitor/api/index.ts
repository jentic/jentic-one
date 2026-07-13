/**
 * Monitor api-layer barrel.
 *
 * Re-exports the service-tier hooks + UI types for the module's own
 * components/pages. Components import from `@/modules/monitor/api`, never from
 * `client.ts` directly (client is the repository tier, reached only via hooks).
 */
export {
	useExecutions,
	useExecution,
	useExecutionStats,
	useJobs,
	useJob,
	useCancelJob,
	useEvents,
	useAcknowledgeEvent,
	useEventStream,
	useAudit,
	useActorForTrace,
	useActorForJob,
	useActors,
	monitorKeys,
} from '@/modules/monitor/api/hooks';
export type { LiveStreamStatus } from '@/modules/monitor/api/hooks';

export { MonitorApiError } from '@/modules/monitor/api/client';
export type {
	ListExecutionsParams,
	ListJobsParams,
	ListEventsParams,
	ListAuditParams,
	ListActorsParams,
	ExecutionStatsParams,
} from '@/modules/monitor/api/client';

export {
	MONITOR_TABS,
	toExecutionStatus,
	toJobStatus,
	isTerminalJobStatus,
} from '@/modules/monitor/api/types';
export type {
	MonitorTab,
	ExecutionStatusUi,
	JobStatusUi,
	AuditActor,
} from '@/modules/monitor/api/types';

// Re-export the generated models the views render, so view components consume
// them through the module's api barrel rather than reaching into the
// @/shared/api facade directly (which the layering ESLint rule forbids).
export type {
	ExecutionResponse,
	ExecutionListResponse,
	ExecutionStatsResponse,
	DailyExecutionBucket,
	TopOperation,
	JobResponse,
	JobListResponse,
	EventResponse,
	EventListResponse,
	AuditResponse,
	AuditListResponse,
	ActorSummaryResponse,
	ActorListResponse,
} from '@/shared/api';
export { EventSeverity, AuditTargetType } from '@/shared/api';
