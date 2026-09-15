/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PermissionRuleSchema } from './PermissionRuleSchema';
/**
 * Patch permission rules — add and/or remove.
 */
export type PermissionsPatchRequest = {
    add?: (Array<PermissionRuleSchema> | null);
    remove?: (Array<number> | null);
};

