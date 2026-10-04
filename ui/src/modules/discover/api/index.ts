/**
 * Discover api-layer barrel.
 *
 * Re-exports the service-tier hooks + UI types for the module's own
 * components/pages. Components import from `@/modules/discover/api`, never
 * from `client.ts` directly (client is the repository tier, reached only via
 * hooks).
 */
export {
	useDiscoverCatalog,
	useCatalogInWorkspace,
	useCatalogJump,
	useOperationPreview,
	useImportCatalogApi,
	useRefreshCatalog,
	setImportPollIntervalForTests,
	OPERATION_PREVIEW_PAGE_SIZE,
} from '@/modules/discover/api/hooks';

export { useWorkspaceDigest } from '@/modules/discover/api/workspaceDigest';
export type {
	WorkspaceDigest,
	WorkspaceDigestRow,
	AttentionEntry,
	AttentionId,
} from '@/modules/discover/api/workspaceDigest';

export type { DiscoveryEntity, CatalogFilter } from '@/modules/discover/api/types';

// Re-export the generated preview type the views render, so view components
// consume it through the module's api barrel rather than reaching into the
// @/shared/api facade directly (which the layering ESLint rule forbids).
export type { PreviewOperationResponse } from '@/shared/api';
