/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Payload for first-run admin creation (one-time setup).
 */
export type CreateAdminRequest = {
    /**
     * Email address of the first admin.
     */
    email: string;
    /**
     * First name of the first admin; defaults to `Admin`.
     */
    first_name?: string;
    /**
     * Last name of the first admin; defaults to `User`.
     */
    last_name?: string;
    /**
     * Password for the first admin.
     */
    password: string;
};

