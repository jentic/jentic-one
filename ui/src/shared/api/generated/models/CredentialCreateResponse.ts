/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CredentialRedactedResponse } from './CredentialRedactedResponse';
/**
 * Create response: redacted + secret shown once.
 */
export type CredentialCreateResponse = {
    credential: CredentialRedactedResponse;
    secret: Record<string, any>;
    /**
     * Advisory warnings from create — e.g. the credential's API scope matches no imported API identity, so execution through it would fail until a matching API is imported. The credential is created regardless; null when there is nothing to flag.
     */
    warnings?: (Array<string> | null);
};

