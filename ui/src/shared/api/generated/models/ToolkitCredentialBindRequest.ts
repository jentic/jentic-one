/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { jentic_one__control__web__schemas__toolkits__PermissionRuleSchema } from './jentic_one__control__web__schemas__toolkits__PermissionRuleSchema';
/**
 * Bind a credential to a toolkit.
 */
export type ToolkitCredentialBindRequest = {
    /**
     * Convenience flag: bind with a single `allow` rule that matches every request for this binding's vendor. Mutually exclusive with `permissions`.
     */
    allow_all?: boolean;
    credential_id: string;
    permissions?: (Array<jentic_one__control__web__schemas__toolkits__PermissionRuleSchema> | null);
};

