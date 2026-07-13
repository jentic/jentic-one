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
} from '@/modules/workspace/api/hooks';
export type { UseImportSpec } from '@/modules/workspace/api/hooks';
export type { UseApiOperations } from '@/modules/workspace/api/hooks';

export { WorkspaceApiError } from '@/modules/workspace/api/client';

export { parseSpecOperations, opDetailKey } from '@/modules/workspace/api/specOperations';
export type { ParsedSpec, SpecOperationDetail } from '@/modules/workspace/api/specOperations';

export { encodeApiId, formatApiKey } from '@/modules/workspace/api/apiId';
export type { ApiKey } from '@/modules/workspace/api/apiId';

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
} from '@/modules/workspace/api/types';
