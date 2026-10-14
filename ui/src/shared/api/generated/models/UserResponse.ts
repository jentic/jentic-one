/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Permissions } from './Permissions';
/**
 * User representation in API responses.
 */
export type UserResponse = {
    /**
     * Whether the user account is enabled.
     */
    active: boolean;
    /**
     * How the user authenticates: `local` (password) or `external`.
     */
    auth_provider: string;
    /**
     * When the user was created (RFC 3339).
     */
    created_at: string;
    /**
     * Email address of the user.
     */
    email: string;
    /**
     * Subject ID from the external identity provider; null for local users.
     */
    external_subject_id?: (string | null);
    /**
     * First name of the user.
     */
    first_name: string;
    /**
     * Unique ID of the user.
     */
    id: string;
    /**
     * Invitation state: `pending`, `redeemed`, `accepted` or `expired`. `expired` means a pending invite whose token has lapsed and should be reissued.
     */
    invite_state: string;
    /**
     * Last name of the user.
     */
    last_name: string;
    /**
     * Whether the user must change their password at next use.
     */
    must_change_password: boolean;
    /**
     * Full display name: first name and last name.
     */
    name: string;
    permissions: Permissions;
    /**
     * When the user was last updated (RFC 3339); null if never updated.
     */
    updated_at?: (string | null);
};

