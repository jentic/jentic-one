/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Permission rule response (includes system fields).
 */
export type PermissionRuleReadSchema = {
    _comment?: (string | null);
    _system?: boolean;
    effect: PermissionRuleReadSchema.effect;
    match_mode?: PermissionRuleReadSchema.match_mode;
    methods?: (Array<string> | null);
    operations?: (Array<string> | null);
    path?: (string | null);
};
export namespace PermissionRuleReadSchema {
    export enum effect {
        ALLOW = 'allow',
        DENY = 'deny',
    }
    export enum match_mode {
        REGEX = 'regex',
        PREFIX = 'prefix',
        EXACT = 'exact',
    }
}

