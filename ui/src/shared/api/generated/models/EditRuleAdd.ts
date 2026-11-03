/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PermissionRuleSchema } from './PermissionRuleSchema';
/**
 * One rule to add to the binding.
 */
export type EditRuleAdd = {
    /**
     * ID of the rule to insert this one before (first match wins); appended when omitted.
     */
    before?: (string | null);
    /**
     * The rule to add.
     */
    rule: PermissionRuleSchema;
};

