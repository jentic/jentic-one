/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { UserResponse } from './UserResponse';
/**
 * Response after user creation including invite token.
 */
export type UserCreatedResponse = {
    /**
     * When the invite token expires (RFC 3339).
     */
    invite_expires_at: string;
    /**
     * One-time invite token the user redeems to set a password.
     */
    invite_token: string;
    user: UserResponse;
};

