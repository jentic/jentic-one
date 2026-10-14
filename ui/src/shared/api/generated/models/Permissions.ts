/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EffectivePermission } from './EffectivePermission';
/**
 * Structured permissions view: assigned + effective.
 */
export type Permissions = {
    /**
     * Permissions assigned directly to the user.
     */
    assigned: Array<string>;
    /**
     * All permissions the user holds, including those implied by assigned ones.
     */
    effective: Array<EffectivePermission>;
};

