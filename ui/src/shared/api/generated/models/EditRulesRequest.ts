/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EditRuleAdd } from './EditRuleAdd';
import type { PermissionRuleSchema } from './PermissionRuleSchema';
/**
 * A change to the binding's rules: `add`/`remove`, or the full list in `set`.
 */
export type EditRulesRequest = {
    /**
     * Rules to add (at most 100).
     */
    add?: Array<EditRuleAdd>;
    /**
     * IDs of rules to remove (at most 100).
     */
    remove?: Array<string>;
    /**
     * The full rule list to replace the current one with, in order (at most 100). Not combined with add or remove.
     */
    set?: (Array<PermissionRuleSchema> | null);
};

