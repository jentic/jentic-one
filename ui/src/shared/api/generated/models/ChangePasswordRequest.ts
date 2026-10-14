/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Payload for changing own password.
 */
export type ChangePasswordRequest = {
    /**
     * The caller's current password.
     */
    current_password: string;
    /**
     * The new password to set.
     */
    new_password: string;
};

