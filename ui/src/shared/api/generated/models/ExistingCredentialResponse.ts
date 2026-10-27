/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A credential the viewer already holds for the target.
 *
 * ``granted_scopes`` / ``missing_scopes`` are set for OAuth credentials
 * only; a static credential's upstream permissions are not visible to the
 * platform. ``can_reauthorize`` is false whenever another agent is bound
 * (``other_bound_agent_ids``) — widening it would widen theirs too.
 */
export type ExistingCredentialResponse = {
    /**
     * Whether the viewer may bind the credential to the agent.
     */
    can_bind: boolean;
    /**
     * Whether the viewer may widen the credential's scopes.
     */
    can_reauthorize: boolean;
    /**
     * ID of the credential.
     */
    credential_id: string;
    /**
     * Scopes the credential holds; null for a non-OAuth credential.
     */
    granted_scopes?: (Array<string> | null);
    /**
     * Requested scopes the credential lacks; null for a non-OAuth credential.
     */
    missing_scopes?: (Array<string> | null);
    /**
     * Name of the credential.
     */
    name: string;
    /**
     * Other agents bound to the credential.
     */
    other_bound_agent_ids?: Array<string>;
    /**
     * The credential's type.
     */
    type: string;
};

