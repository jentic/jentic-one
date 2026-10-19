/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Scopes to add to the binding's OAuth credential.
 */
export type EditScopesRequest = {
    /**
     * Scopes to add (at most 100). Confirming runs a vendor consent on the credential; other agents bound to it gain them too.
     */
    add?: Array<string>;
};

