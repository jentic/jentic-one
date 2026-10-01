/**
 * Workspace api-layer barrel.
 *
 * Re-exports the service-tier hooks + UI types for the module's own
 * components/pages. Components import from `@/modules/workspace/api`, never from
 * `client.ts` directly (the repository tier, reached only via hooks).
 */
export {
	useWorkspaceApi,
	useApiOperations,
	useApiRevisions,
	useApiSpec,
	useApiAuthRequirement,
	useRevisionActions,
	useOverlays,
	useOverlayActions,
	useSnoozeCatalogUpdate,
	useDeleteApi,
	useReimportFromCatalog,
	useApiNotes,
	workspaceKeys,
} from '@/modules/workspace/api/hooks';
export type { UseApiOperations, UsePagedList } from '@/modules/workspace/api/hooks';

export { WorkspaceApiError } from '@/modules/workspace/api/client';
export { workspaceApiDisplayTitle } from '@/modules/workspace/api/adapters';

// Cross-module reads the API hub joins in (credentials → agents, 7-day usage).
// They live in shared (the Library catalog panel needs them too); re-exported
// here so the hub's views keep one api-barrel entry point.
export {
	useApiAccessIndex,
	useAgentAccess,
	agentsExhaustive,
} from '@/shared/credentials/api/apiAccess';
export { useApiUsageWeek, apiUsageKeyFor } from '@/shared/hooks';
export { callsInWeek } from '@/shared/credentials/api/apiHealth';

export {
	parseSpecOperations,
	opDetailKey,
	specAuthRequirement,
} from '@/modules/workspace/api/specOperations';
export type {
	ParsedSpec,
	SpecOperationDetail,
	SpecAuthRequirement,
} from '@/modules/workspace/api/specOperations';

export {
	shortOverlayId,
	shortRevisionId,
	formatDateTime,
	summarizeOverlayActions,
	overlayLifecycle,
	overlayLifecycleNote,
	OVERLAY_LIFECYCLE_LABEL,
	revisionStateLabel,
	revisionOriginLabel,
	overlayForRevision,
	revisionChangeSummary,
	diffBaseFor,
	describeLastChange,
	describeServingState,
} from '@/modules/workspace/api/insights';
export type { OverlayLifecycle, SpecDiffBase } from '@/modules/workspace/api/insights';

export { diffSpecs } from '@/modules/workspace/api/specDiff';
export type { SpecDiffEntry, SpecDiffKind, SpecDiffResult } from '@/modules/workspace/api/specDiff';

export { encodeApiId, formatApiKey } from '@/modules/workspace/api/apiId';
export type { ApiKey } from '@/modules/workspace/api/apiId';

export type {
	ApiNote,
	ApiRef,
	WorkspaceApi,
	ApiOperation,
	ApiRevision,
	RevisionState,
	RevisionOrigin,
	Overlay,
	OverlayStatus,
	CursorPage,
	ImportJob,
	JobStatus,
	ImportSource,
} from '@/modules/workspace/api/types';
