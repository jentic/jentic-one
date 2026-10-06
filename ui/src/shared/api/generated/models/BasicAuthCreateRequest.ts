/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { APIReferenceRequest } from './APIReferenceRequest';
import type { RuntimeConfig } from './RuntimeConfig';
/**
 * Create request for basic credentials.
 */
export type BasicAuthCreateRequest = {
    api: APIReferenceRequest;
    /**
     * After saving, make one read call with the credential and return the verdict as `check`. The credential is saved either way.
     */
    check?: boolean;
    name: string;
    password: string;
    provider?: string;
    runtime_config?: (RuntimeConfig | null);
    server_variables?: (Record<string, string> | null);
    type: string;
    username: string;
};

