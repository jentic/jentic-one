/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { jentic_one__control__web__schemas__access_requests__PermissionRuleSchema } from './jentic_one__control__web__schemas__access_requests__PermissionRuleSchema';
/**
 * A single item amendment.
 */
export type AmendItemSchema = {
    item_id: string;
    resource_id?: (string | null);
    rules?: (Array<jentic_one__control__web__schemas__access_requests__PermissionRuleSchema> | null);
    to_id?: (string | null);
};

