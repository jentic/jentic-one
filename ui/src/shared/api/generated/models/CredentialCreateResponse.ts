/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CredentialCheckResponse } from './CredentialCheckResponse';
import type { CredentialRedactedResponse } from './CredentialRedactedResponse';
/**
 * Create response: redacted + secret shown once.
 */
export type CredentialCreateResponse = {
    /**
     * The save-time check, present when the request set `check`.
     */
    check?: (CredentialCheckResponse | null);
    credential: CredentialRedactedResponse;
    secret: Record<string, any>;
};

