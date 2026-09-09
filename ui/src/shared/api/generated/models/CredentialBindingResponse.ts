/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ServedApiRef } from './ServedApiRef';
/**
 * Direct agent↔credential binding representation in API responses.
 */
export type CredentialBindingResponse = {
    agent_id: string;
    bound_at: string;
    credential_id: string;
    id: string;
    name?: (string | null);
    serves?: Array<ServedApiRef>;
    suspended: boolean;
};

