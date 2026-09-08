/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { APIReferenceRequest } from './APIReferenceRequest';
import type { RuntimeConfig } from './RuntimeConfig';
/**
 * Create request for sigv4 (AWS Signature V4) credentials.
 */
export type Sigv4CreateRequest = {
    /**
     * AWS access key id (public identifier).
     */
    access_key_id: string;
    /**
     * Loose (vendor, name, version) API identity tuple.
     */
    api: APIReferenceRequest;
    /**
     * Signing region, e.g. 'us-east-1'.
     */
    aws_region: string;
    /**
     * Signing service, e.g. 'aoss', 'execute-api', 's3'.
     */
    aws_service: string;
    /**
     * Human-readable label for the credential.
     */
    name: string;
    /**
     * Credential provider; 'static' for stored secrets.
     */
    provider?: string;
    runtime_config?: (RuntimeConfig | null);
    /**
     * AWS secret access key. Stored encrypted; never returned after create.
     */
    secret_access_key: string;
    server_variables?: (Record<string, string> | null);
    /**
     * Optional temporary-credential session token (STS). Expires; re-save when it does.
     */
    session_token?: (string | null);
    type: string;
};

