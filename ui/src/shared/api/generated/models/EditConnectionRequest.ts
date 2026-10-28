/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EditRulesRequest } from './EditRulesRequest';
import type { EditScopesRequest } from './EditScopesRequest';
/**
 * Ask to change an existing credential binding; a human approves the change.
 */
export type EditConnectionRequest = {
    /**
     * The binding's agent. Required for humans; agent callers must omit it.
     */
    agent_id?: (string | null);
    /**
     * A human approving their own change as they make it: it is written at once, with no review. A change that adds scopes starts the vendor consent instead (`challenge`). Agent callers are 403.
     */
    apply?: boolean;
    /**
     * The binding's credential; it must already be bound to the agent.
     */
    credential_id: string;
    /**
     * Why the change is needed, shown to the reviewer.
     */
    reason?: (string | null);
    /**
     * Switch a suspended binding back on; a no-op if it is already active.
     */
    resume?: boolean;
    /**
     * The rule change. While the binding points at a shared rule set, it changes that set's rules.
     */
    rules?: (EditRulesRequest | null);
    /**
     * The scope change. OAuth credentials connected through an authorization-code or device-authorization app only; others are 422 scopes_not_supported.
     */
    scopes?: (EditScopesRequest | null);
};

