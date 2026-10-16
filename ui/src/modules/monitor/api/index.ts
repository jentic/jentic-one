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
	useUsageStats,
	useJobs,
	useJob,
	useCancelJob,
	useEventStream,
	useEventFeed,
	useAudit,
	useActorForTrace,
	useActors,
	monitorKeys,
} from '@/modules/monitor/api/hooks';
export type { LiveStreamStatus } from '@/modules/monitor/api/hooks';

// Usage-window plumbing for `useUsageStats` / `UsageCharts` call sites, so
// every screen shares one clock step, window shape, and top-N (one cache key).
export {
	USAGE_TOP_LIMIT,
	USAGE_WINDOW_STEP_MS,
	useCoarseNowSec,
	useUsageWindow,
	usageWindowBounds,
} from '@/modules/monitor/api/usageWindow';

export { MonitorApiError, isMonitorAccessDenied } from '@/modules/monitor/api/client';
export type { ListAuditParams } from '@/modules/monitor/api/client';

export {
	ACTIVITY_SOURCES,
	toExecutionStatus,
	toJobStatus,
	isTerminalJobStatus,
} from '@/modules/monitor/api/types';
export type {
	MonitorView,
	ActivitySource,
	ExecutionStatusUi,
	JobStatusUi,
} from '@/modules/monitor/api/types';

// Re-export the generated models the views render, so view components consume
// them through the module's api barrel rather than reaching into the
// @/shared/api facade directly (which the layering ESLint rule forbids).
export type { ExecutionResponse, UsageResponse, EventResponse, AuditResponse } from '@/shared/api';
export { EventSeverity, AuditTargetType, GroupBy } from '@/shared/api';
