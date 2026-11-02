/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EditRuleView } from './EditRuleView';
import type { EditScopeNote } from './EditScopeNote';
/**
 * What confirming the current revision leaves in place.
 */
export type EditResultAccess = {
    rules: Array<EditRuleView>;
    /**
     * Each scope the change adds or removes: who asked for it, and why.
     */
    scope_notes?: Record<string, EditScopeNote>;
    scopes: Array<string>;
    suspended: boolean;
    /**
     * Whether the edit changes the binding's own rules or its shared rule set.
     */
    target: EditResultAccess.target;
};
export namespace EditResultAccess {
    /**
     * Whether the edit changes the binding's own rules or its shared rule set.
     */
    export enum target {
        BINDING = 'binding',
        RULE_SET = 'rule_set',
    }
}

