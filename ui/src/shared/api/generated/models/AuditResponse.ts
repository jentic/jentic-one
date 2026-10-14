/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Audit entry representation in API responses.
 */
export type AuditResponse = {
    /**
     * The audited action, e.g. `create`, `update` or `revoke`.
     */
    action: string;
    /**
     * ID of the actor that performed the action; null if unknown.
     */
    actor_id?: (string | null);
    /**
     * Session the actor acted under; null if not recorded.
     */
    actor_session_id?: (string | null);
    /**
     * Kind of actor that performed the action, e.g. `user` or `agent`.
     */
    actor_type: string;
    /**
     * Snapshot of the target after the action; null if none.
     */
    after?: (Record<string, any> | null);
    /**
     * Snapshot of the target before the action; null if none.
     */
    before?: (Record<string, any> | null);
    /**
     * Changes between `before` and `after`, keyed `added`, `removed` and `modified` (each modified key maps to `old`/`new`); null when nothing changed.
     */
    diff?: (Record<string, any> | null);
    /**
     * Unique ID of the audit entry.
     */
    id: string;
    /**
     * Client IP address of the triggering request, if recorded.
     */
    ip_address?: (string | null);
    /**
     * ID of the job the action ran under, if any.
     */
    job_id?: (string | null);
    /**
     * When the audited action happened (RFC 3339).
     */
    occurred_at: string;
    /**
     * Surface the action was initiated from, e.g. `dashboard`, `cli`, `api`, `agent`, `mcp` or `system`; null if not recorded.
     */
    origin?: (string | null);
    /**
     * Reason recorded for the action; null if none was given.
     */
    reason?: (string | null);
    /**
     * ID of the request that triggered the action, if recorded.
     */
    request_id?: (string | null);
    /**
     * ID of the entity the action was performed on.
     */
    target_id: string;
    /**
     * ID of the target's parent entity; null when not applicable.
     */
    target_parent_id?: (string | null);
    /**
     * Type of the entity the action was performed on.
     */
    target_type: string;
    /**
     * Trace ID of the triggering request, if recorded.
     */
    trace_id?: (string | null);
    /**
     * User-Agent of the triggering request, if recorded.
     */
    user_agent?: (string | null);
};

