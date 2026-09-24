/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CredentialBindingEntry } from './CredentialBindingEntry';
import type { ToolkitBindingEntry } from './ToolkitBindingEntry';
/**
 * Identity response for an agent actor.
 */
export type MeAgent = {
    approved_by?: (string | null);
    credential_bindings?: Array<CredentialBindingEntry>;
    id: string;
    name: string;
    parent_agent_id?: (string | null);
    scopes: Array<string>;
    status: string;
    token_scopes: Array<string>;
    toolkit_bindings: Array<ToolkitBindingEntry>;
    type?: string;
};

