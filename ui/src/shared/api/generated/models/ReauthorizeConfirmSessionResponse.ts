/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The agent is bound to ``credential_id``; open ``authorize_url`` to grant more scopes.
 */
export type ReauthorizeConfirmSessionResponse = {
    /**
     * Vendor sign-in URL that grants the wider scopes.
     */
    authorize_url: string;
    /**
     * ID of the credential the agent is bound to.
     */
    credential_id: string;
    kind?: string;
};

