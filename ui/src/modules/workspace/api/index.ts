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
	useDeleteApi,
	useImportSpec,
	workspaceKeys,
} from '@oss-internal/modules/workspace/api/hooks';
export type { UseImportSpec } from '@oss-internal/modules/workspace/api/hooks';
export type { UseApiOperations } from '@oss-internal/modules/workspace/api/hooks';

export { WorkspaceApiError } from '@oss-internal/modules/workspace/api/client';

export {
	parseSpecOperations,
	opDetailKey,
} from '@oss-internal/modules/workspace/api/specOperations';
export type {
	ParsedSpec,
	SpecOperationDetail,
} from '@oss-internal/modules/workspace/api/specOperations';

export { encodeApiId, formatApiKey } from '@oss-internal/modules/workspace/api/apiId';
export type { ApiKey } from '@oss-internal/modules/workspace/api/apiId';

export type {
	ApiRef,
	WorkspaceApi,
	ApiOperation,
	ApiRevision,
	RevisionState,
	CursorPage,
	ImportJob,
	JobStatus,
	ImportSource,
} from '@oss-internal/modules/workspace/api/types';
