/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * What one test call says about a credential.
 */
export enum CredentialCheckStatus {
    OK = 'ok',
    BAD_KEY = 'bad_key',
    EXPIRED = 'expired',
    MISSING_SCOPE = 'missing_scope',
    WRONG_BASE_URL = 'wrong_base_url',
    UNREACHABLE = 'unreachable',
    UNTESTED = 'untested',
}
