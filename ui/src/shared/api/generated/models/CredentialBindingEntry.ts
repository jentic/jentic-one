/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ServedApiRef } from './ServedApiRef';
/**
 * Direct agent↔credential binding summary for the /me response (theme 5 phase 1).
 */
export type CredentialBindingEntry = {
    bound_at: string;
    credential_id: string;
    name?: (string | null);
    rule_set_id?: (string | null);
    serves?: Array<ServedApiRef>;
    suspended?: boolean;
};

