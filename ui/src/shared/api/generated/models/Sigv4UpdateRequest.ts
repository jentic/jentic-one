/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RuntimeConfig } from './RuntimeConfig';
/**
 * Update request for sigv4 credentials (key rotation / scope edit).
 */
export type Sigv4UpdateRequest = {
    access_key_id?: (string | null);
    active?: (boolean | null);
    aws_region?: (string | null);
    aws_service?: (string | null);
    clear_session_token?: boolean;
    name?: (string | null);
    runtime_config?: (RuntimeConfig | null);
    secret_access_key?: (string | null);
    server_variables?: (Record<string, string> | null);
    session_token?: (string | null);
    type: string;
};

