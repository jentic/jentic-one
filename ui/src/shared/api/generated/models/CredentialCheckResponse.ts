/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CredentialCheckStatus } from './CredentialCheckStatus';
/**
 * What one test call made with the stored credential says about it.
 */
export type CredentialCheckResponse = {
    /**
     * The call the check made, e.g. `GET https://api.example.com/v1/me`, query values redacted.
     */
    probe?: (string | null);
    /**
     * One sentence naming the cause. Never contains the secret.
     */
    reason: string;
    /**
     * `ok`, or why the credential fails: `bad_key`, `expired`, `missing_scope`, `wrong_base_url`, `unreachable`. `untested` means no call could be made or the answer said nothing about the credential; `reason` says which.
     */
    status: CredentialCheckStatus;
    /**
     * The HTTP status the API answered, when it answered.
     */
    upstream_status?: (number | null);
};

