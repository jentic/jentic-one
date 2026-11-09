/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One scope a connection to the API could ask for.
 */
export type ConnectScopeOptionResponse = {
    /**
     * What the scope lets a holder do.
     */
    classification?: ('read' | 'write' | 'admin' | null);
    /**
     * Asked for when a request names no scopes.
     */
    default?: boolean;
    description?: string;
    name: string;
    /**
     * Whether a request may name it here: in the connect source's catalog, or declared by the spec when no catalog applies.
     */
    requestable: boolean;
};

