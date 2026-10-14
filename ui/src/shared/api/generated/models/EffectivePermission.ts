/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A single effective permission with provenance.
 */
export type EffectivePermission = {
    /**
     * Assigned permission that implies this one; null when it is assigned directly.
     */
    implied_by?: (string | null);
    /**
     * Permission name.
     */
    name: string;
};

