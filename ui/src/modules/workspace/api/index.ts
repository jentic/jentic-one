/**
 * Workspace api-layer barrel.
 *
 * Re-exports the service-tier hooks + UI types for the module's own
 * components/pages. Components import from `@/modules/workspace/api`, never from
 * `client.ts` directly (the repository tier, reached only via hooks).
 */
export {
	useWorkspaceApis,
	useWorkspaceApi,
	useApiOperations,
	useApiRevisions,
	useApiSpec,
	useRevisionActions,
	useOverlays,
	useOverlayActions,
	useSnoozeCatalogUpdate,
	useDeleteApi,
	useReimportFromCatalog,
	useWorkspaceTraffic,
	useApiAgentTraffic,
	useWorkspaceCredentials,
	useCredentialBindings,
	usageKeyFor,
	workspaceKeys,
} from '@/modules/workspace/api/hooks';
export type { Credential } from '@/shared/credentials/api';
export type {
	UseApiOperations,
	UsePagedList,
	WorkspaceTraffic,
	WorkspaceCredentials,
} from '@/modules/workspace/api/hooks';

export { WorkspaceApiError } from '@/modules/workspace/api/client';

export { parseSpecOperations, opDetailKey } from '@/modules/workspace/api/specOperations';
export type { ParsedSpec, SpecOperationDetail } from '@/modules/workspace/api/specOperations';

export {
	shortOverlayId,
	shortRevisionId,
	formatDateTime,
	formatAgo,
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
	lastChangeEvent,
	importedBy,
	pendingOverlayCount,
	apiAttention,
	API_ATTENTION_LABEL,
	parseUsageCaller,
} from '@/modules/workspace/api/insights';
export type {
	OverlayLifecycle,
	SpecDiffBase,
	ChangeEvent,
	ApiAttention,
	UsageCaller,
} from '@/modules/workspace/api/insights';

export { diffSpecs } from '@/modules/workspace/api/specDiff';
export type { SpecDiffEntry, SpecDiffKind, SpecDiffResult } from '@/modules/workspace/api/specDiff';

export { encodeApiId, formatApiKey } from '@/modules/workspace/api/apiId';
export type { ApiKey } from '@/modules/workspace/api/apiId';

export type {
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
	UsageRow,
} from '@/modules/workspace/api/types';
export { USAGE_WINDOW_DAYS } from '@/modules/workspace/api/types';
