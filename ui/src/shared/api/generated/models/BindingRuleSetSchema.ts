/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PermissionRuleReadSchema } from './PermissionRuleReadSchema';
/**
 * The shared rule set a binding points at.
 */
export type BindingRuleSetSchema = {
    /**
     * True for a curated set, which an edit can copy but never change.
     */
    curated: boolean;
    /**
     * ID of the rule set (`prs_…`).
     */
    id: string;
    /**
     * Name of the rule set.
     */
    name: string;
    /**
     * The set's rules, in evaluation order. They apply instead of `data`.
     */
    rules: Array<PermissionRuleReadSchema>;
};

