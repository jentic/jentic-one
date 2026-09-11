/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { jentic_one__control__web__schemas__permission_rules__PermissionRuleSchema } from './jentic_one__control__web__schemas__permission_rules__PermissionRuleSchema';
/**
 * Patch permission rules — add and/or remove.
 */
export type PermissionsPatchRequest = {
    add?: (Array<jentic_one__control__web__schemas__permission_rules__PermissionRuleSchema> | null);
    remove?: (Array<number> | null);
};

