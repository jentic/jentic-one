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
	useOperationPreview,
	useImportCatalogApi,
	useRefreshCatalog,
	discoverKeys,
	setImportPollIntervalForTests,
	OPERATION_PREVIEW_PAGE_SIZE,
} from '@/modules/discover/api/hooks';

export type {
	UseDiscoverCatalogResult,
	UseOperationPreviewResult,
} from '@/modules/discover/api/hooks';

export { DiscoverApiError } from '@/modules/discover/api/client';

export type { DiscoveryEntity, CatalogFilter } from '@/modules/discover/api/types';

// Re-export the generated preview types the views render, so view components
// consume them through the module's api barrel rather than reaching into the
// @/shared/api facade directly (which the layering ESLint rule forbids).
export type {
	OperationPreviewListResponse,
	PreviewOperationResponse,
	PreviewInfoResponse,
} from '@/shared/api';
