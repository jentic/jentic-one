/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AfterExpiry } from './AfterExpiry';
import type { GrantEndReason } from './GrantEndReason';
/**
 * The binding a `grant_renewal` hold would renew, as it stands now.
 */
export type HeldRenewalContextResponse = {
    /**
     * What a call through it gets once it has ended: `hold` or `refuse`.
     */
    after_expiry?: AfterExpiry;
    /**
     * Whether the caller may re-grant access (else it can only deny): a person who owns the agent and the credential, or an `org:admin`.
     */
    can_renew?: boolean;
    /**
     * Which limit ended it: `end_date`, `idle` or `uses`; null if it holds again (renewed since).
     */
    expired_reason?: (GrantEndReason | null);
    /**
     * Its current end date.
     */
    expires_at?: (string | null);
    /**
     * Whether its owner is warned before it ends (end date or idle limit).
     */
    idle_warning?: boolean;
    /**
     * Its last recorded use; null if never used.
     */
    last_used_at?: (string | null);
    /**
     * Its current idle limit, as an ISO 8601 duration.
     */
    max_idle_period?: (string | null);
    /**
     * Its use count (1 is single use).
     */
    max_quota?: (number | null);
    /**
     * Uses left under its count.
     */
    quota_remaining?: (number | null);
    /**
     * Whether approving with `renewal` renews the binding here. False where this deployment's admin surface cannot renew (it runs apart from the control database), and `can_renew` is then unknown: renew the binding through `PATCH /agents/{agent_id}/credentials/{credential_id}`, then approve without `renewal`.
     */
    renew_on_decide?: boolean;
};

