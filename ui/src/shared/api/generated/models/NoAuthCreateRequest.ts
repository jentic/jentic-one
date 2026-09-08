/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { APIReferenceRequest } from './APIReferenceRequest';
import type { RuntimeConfig } from './RuntimeConfig';
/**
 * Create request for no_auth credentials.
 *
 * A no-auth credential carries no secret — it represents "this API is called
 * without authentication". It still exists as a credential row so a toolkit
 * binding (and its permission rules) can hang off it, and the broker resolves
 * it as a no-op auth (see broker credential resolver / injection).
 */
export type NoAuthCreateRequest = {
    api: APIReferenceRequest;
    name: string;
    provider?: string;
    runtime_config?: (RuntimeConfig | null);
    server_variables?: (Record<string, string> | null);
    type: string;
};

