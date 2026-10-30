/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AuthCodeConfirmSessionResponse } from './AuthCodeConfirmSessionResponse';
import type { ConnectedConfirmSessionResponse } from './ConnectedConfirmSessionResponse';
import type { DeviceAuthorizationConfirmSessionResponse } from './DeviceAuthorizationConfirmSessionResponse';
/**
 * The opened edit session.
 */
export type EditConnectionResponse = {
    /**
     * Review page to relay to the human approver.
     */
    approval_url: string;
    /**
     * With `apply` and added scopes: the vendor sign-in to complete.
     */
    challenge?: ((DeviceAuthorizationConfirmSessionResponse | AuthCodeConfirmSessionResponse | ConnectedConfirmSessionResponse) | null);
    /**
     * Capability to poll the session's status; treat it as a secret.
     */
    poll_token: string;
    /**
     * `edit` when the change needs no vendor step; otherwise the vendor flow confirming it runs (`authorization_code` or `device_authorization`).
     */
    resolved_flow: string;
    /**
     * ID of the session's current revision (`csr_…`).
     */
    revision_id: string;
    /**
     * ID of the edit session.
     */
    session_id: string;
    /**
     * `pending` waits on a human's approval; with `apply`, `applied` is written and `consenting` waits on the vendor sign-in in `challenge`.
     */
    status?: EditConnectionResponse.status;
};
export namespace EditConnectionResponse {
    /**
     * `pending` waits on a human's approval; with `apply`, `applied` is written and `consenting` waits on the vendor sign-in in `challenge`.
     */
    export enum status {
        PENDING = 'pending',
        APPLIED = 'applied',
        CONSENTING = 'consenting',
    }
}

