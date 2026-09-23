/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { jentic_one__control__web__schemas__permission_rules__PermissionRuleSchema } from './jentic_one__control__web__schemas__permission_rules__PermissionRuleSchema';
export type ConfirmSessionRequest = {
    agent_id?: (string | null);
    confirmed_scopes: Array<string>;
    permission_rules?: Array<jentic_one__control__web__schemas__permission_rules__PermissionRuleSchema>;
};

