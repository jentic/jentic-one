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
	useImportSpec,
	useReimportFromCatalog,
	workspaceKeys,
} from '@/modules/workspace/api/hooks';
export type { UseImportSpec } from '@/modules/workspace/api/hooks';
export type { UseApiOperations } from '@/modules/workspace/api/hooks';

export { WorkspaceApiError } from '@/modules/workspace/api/client';

export { parseSpecOperations, opDetailKey } from '@/modules/workspace/api/specOperations';
export type { ParsedSpec, SpecOperationDetail } from '@/modules/workspace/api/specOperations';

export {
	shortOverlayId,
	shortRevisionId,
	summarizeOverlayActions,
	overlayLifecycle,
	OVERLAY_LIFECYCLE_LABEL,
	revisionOriginLabel,
	overlayForRevision,
	revisionChangeSummary,
	describeLastChange,
	describeServingState,
} from '@/modules/workspace/api/insights';
export type { OverlayLifecycle } from '@/modules/workspace/api/insights';

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
} from '@/modules/workspace/api/types';
