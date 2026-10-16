/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BindingRuleSetSchema } from './BindingRuleSetSchema';
import type { PermissionRuleReadSchema } from './PermissionRuleReadSchema';
/**
 * A binding's permission rules plus the rest of what it grants.
 */
export type BindingAccessResponse = {
    /**
     * The binding's permission rules, in evaluation order.
     */
    data: Array<PermissionRuleReadSchema>;
    /**
     * Scopes the credential's OAuth grant holds; null when it has none.
     */
    granted_scopes?: (Array<string> | null);
    /**
     * The shared rule set the binding points at; null when its own rules (`data`) apply. While a set is attached, `data` is dormant.
     */
    rule_set?: (BindingRuleSetSchema | null);
    /**
     * True when the binding is suspended and the agent cannot use it.
     */
    suspended: boolean;
};

