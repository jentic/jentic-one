/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EditRuleSetView } from './EditRuleSetView';
import type { EditRuleView } from './EditRuleView';
/**
 * The binding as the edit session recorded it when it opened.
 */
export type EditCurrentAccess = {
    granted_scopes?: (Array<string> | null);
    /**
     * The attached shared rule set; its rules apply instead.
     */
    rule_set?: (EditRuleSetView | null);
    /**
     * The binding's own rules.
     */
    rules: Array<EditRuleView>;
    suspended: boolean;
};

