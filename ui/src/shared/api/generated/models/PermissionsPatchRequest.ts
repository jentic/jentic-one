/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PermissionRuleSchema } from './PermissionRuleSchema';
/**
 * Patch permission rules — add and/or remove.
 */
export type PermissionsPatchRequest = {
    /**
     * Rules to append after the existing user rules.
     */
    add?: (Array<PermissionRuleSchema> | null);
    /**
     * Zero-based indexes of user rules (excluding system rules) to delete; applied before `add`.
     */
    remove?: (Array<number> | null);
};

