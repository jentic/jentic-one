/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request body for creating a new user.
 */
export type UserCreateRequest = {
    /**
     * Email address of the new user; must be unique.
     */
    email: string;
    /**
     * First name of the new user.
     */
    first_name: string;
    /**
     * Last name of the new user.
     */
    last_name: string;
    /**
     * Permissions to assign to the new user; the caller may only grant permissions they hold. Defaults to none.
     */
    permissions?: Array<string>;
};

