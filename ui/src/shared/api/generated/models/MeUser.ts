/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Identity response for a user actor.
 */
export type MeUser = {
    /**
     * Whether the caller's token holds `org:admin`.
     */
    admin: boolean;
    /**
     * Email address of the user.
     */
    email: string;
    /**
     * Unique ID of the user.
     */
    id: string;
    /**
     * Whether the user must change their password; most endpoints return 403 until they do.
     */
    must_change_password: boolean;
    /**
     * Display name of the user.
     */
    name: string;
    /**
     * Permissions carried by the caller's token.
     */
    permissions: Array<string>;
    /**
     * User status: `active` or `disabled`.
     */
    status: string;
    /**
     * Actor type discriminator; always `user`.
     */
    type?: string;
};

