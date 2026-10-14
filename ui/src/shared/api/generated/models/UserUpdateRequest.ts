/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request body for updating a user.
 */
export type UserUpdateRequest = {
    /**
     * New email address; must be unique. Null means no change.
     */
    email?: (string | null);
    /**
     * New first name; null means no change.
     */
    first_name?: (string | null);
    /**
     * New last name; null means no change.
     */
    last_name?: (string | null);
};

