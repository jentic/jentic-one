/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Current user profile.
 */
export type CurrentUserResponse = {
    /**
     * Whether the user account is enabled.
     */
    active: boolean;
    /**
     * When the user was created (RFC 3339).
     */
    created_at: string;
    /**
     * Email address of the user.
     */
    email: string;
    /**
     * First name of the user.
     */
    first_name: string;
    /**
     * Unique ID of the user.
     */
    id: string;
    /**
     * Last name of the user.
     */
    last_name: string;
    /**
     * Whether the user must change their password before using other endpoints.
     */
    must_change_password: boolean;
    /**
     * The user's effective permissions, including those implied by assigned ones.
     */
    permissions: Array<string>;
    /**
     * When the user was last updated (RFC 3339); null if never updated.
     */
    updated_at?: (string | null);
};

