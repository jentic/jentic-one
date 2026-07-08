/**
 * Dashboard api-layer barrel.
 *
 * Re-exports the service-tier hooks + UI types/derivations for the module's own
 * components/pages. Components import from `@/modules/dashboard/api`, never from
 * `client.ts` directly (client is the repository tier, reached only via hooks).
 */
export {
	usePendingAgents,
	usePendingAccessRequests,
	useAccessRequestsQueue,
	useActionableEvents,
	useRecentExecutions,
	useCatalogSize,
	dashboardKeys,
} from '@oss-internal/modules/dashboard/api/hooks';

export { DashboardApiError } from '@oss-internal/modules/dashboard/api/client';

export {
	isSuccessfulExecution,
	deriveSuccessRate,
	approxCountFromPage,
	formatApproxCount,
} from '@oss-internal/modules/dashboard/api/types';

export type {
	ApproxCount,
	PendingAgentsOverview,
	PendingAccessRequestsOverview,
	AlertsOverview,
	RecentExecutionsOverview,
	CatalogOverview,
} from '@oss-internal/modules/dashboard/api/types';

// Re-export the generated row types the views render, so view components consume
// them through the module's api barrel rather than reaching into the
// @/shared/api facade directly (which the layering ESLint rule forbids).
export type { AgentResponse, EventResponse, ExecutionResponse } from '@oss-internal/shared/api';
export { EventSeverity } from '@oss-internal/shared/api';

// The access-request row type the Pending-requests card renders, surfaced via
// the module barrel so the view doesn't deep-import `@/shared/lib`.
export type { AccessRequest, AccessRequestPage } from '@oss-internal/shared/lib';
