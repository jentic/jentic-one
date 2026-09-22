/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Identity response for a (retired) service-account actor.
 *
 * Served only to callers whose unmigrated ``sak_``/``jntc_live_`` key
 * resolved through the Phase-1 SA-table fallback (theme 8). Deleted with the
 * fallback in Phase 4.
 */
export type MeServiceAccount = {
    approved_by?: (string | null);
    id: string;
    name: string;
    permissions: Array<string>;
    registered_by: string;
    status: string;
    token_permissions: Array<string>;
    type?: string;
};

