/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BindingWarningSchema } from './BindingWarningSchema';
import type { PermissionRuleReadSchema } from './PermissionRuleReadSchema';
/**
 * Credential binding response.
 */
export type ToolkitCredentialBindingResponse = {
    api_name?: (string | null);
    api_vendor?: (string | null);
    bound_at: string;
    /**
     * Catalog identity slug of the bound credential's target API (`domain[/sub-api]`), when recorded. Display-only.
     */
    catalog_api_id?: (string | null);
    credential_id: string;
    credential_type?: (string | null);
    label?: (string | null);
    permissions?: Array<PermissionRuleReadSchema>;
    toolkit_id: string;
    /**
     * Non-fatal bind-time signals — e.g. a binding that landed with zero permission rules (broker denies by default until rules are added).
     */
    warnings?: Array<BindingWarningSchema>;
};

