/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PreviewInfoResponse } from './PreviewInfoResponse';
import type { PreviewOperationResponse } from './PreviewOperationResponse';
/**
 * Capped, offset-paginated operation preview for a catalog entry.
 *
 * Unlike list endpoints (cursor-paginated), the preview uses simple
 * offset/limit pagination deliberately: it reads a single, already-fetched spec
 * document and is hard-capped at ``PREVIEW_MAX_OPERATIONS`` operations, so there
 * is no large/mutating result set that would justify keyset cursors.
 */
export type OperationPreviewListResponse = {
    /**
     * The page of previewed operations.
     */
    data: Array<PreviewOperationResponse>;
    /**
     * The spec's `info` block.
     */
    info: PreviewInfoResponse;
    /**
     * Offset of the returned window.
     */
    offset: number;
    /**
     * Every security scheme some operation in the spec requires, over the whole spec (not just this window or filter).
     */
    required_schemes?: Array<string>;
    /**
     * Slimmed `components.securitySchemes` projection. An `oauth2` scheme lists its flow names in `flows` and each flow's `authorizationUrl`, `tokenUrl`, `refreshUrl` and `scopes` in `flow_details`.
     */
    security_schemes: Record<string, Record<string, any>>;
    /**
     * The first server's URL variables (`servers[0].variables`), each with its `default`, `enum` and `description` when the spec sets them.
     */
    server_variables?: Record<string, Record<string, any>>;
    /**
     * Total operations in the spec (pre-page).
     */
    total: number;
    /**
     * Whether more operations follow this window.
     */
    truncated: boolean;
};

