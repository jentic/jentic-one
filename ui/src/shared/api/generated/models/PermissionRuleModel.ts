/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One agent permission rule (allow/deny).
 */
export type PermissionRuleModel = {
    effect?: PermissionRuleModel.effect;
    method: string;
    path: string;
};
export namespace PermissionRuleModel {
    export enum effect {
        ALLOW = 'allow',
        DENY = 'deny',
    }
}

