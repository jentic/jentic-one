/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ChangePasswordRequest } from '../models/ChangePasswordRequest';
import type { CreateAdminRequest } from '../models/CreateAdminRequest';
import type { CurrentUserResponse } from '../models/CurrentUserResponse';
import type { InviteIssuedResponse } from '../models/InviteIssuedResponse';
import type { InviteState } from '../models/InviteState';
import type { LoginRequest } from '../models/LoginRequest';
import type { LoginResponse } from '../models/LoginResponse';
import type { RedeemInviteRequest } from '../models/RedeemInviteRequest';
import type { SetPermissionsRequest } from '../models/SetPermissionsRequest';
import type { UserCreatedResponse } from '../models/UserCreatedResponse';
import type { UserCreateRequest } from '../models/UserCreateRequest';
import type { UserListResponse } from '../models/UserListResponse';
import type { UserResponse } from '../models/UserResponse';
import type { UserUpdateRequest } from '../models/UserUpdateRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class UsersService {
    /**
     * Log in
     * Authenticate and return a JWT token bundle.
     * @returns LoginResponse Successful Response
     * @throws ApiError
     */
    public static login({
        requestBody,
    }: {
        requestBody: LoginRequest,
    }): CancelablePromise<LoginResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/auth/login',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Refresh session token
     * Re-mint the caller's login JWT before it expires (sliding session).
     *
     * Only user login JWTs are refreshable; permissions and the
     * ``must_change_password`` gate are re-read from the database at re-mint.
     * Refusal modes: 401 ``session_expired`` once the original authentication is
     * older than the absolute window (``admin.auth.session_ttl_seconds``), 401
     * ``invalid_credentials`` for deactivated users, non-user tokens, or opaque
     * (non-JWT) credentials. Clients should then send the user back to login.
     * @returns LoginResponse Successful Response
     * @throws ApiError
     */
    public static refreshSession(): CancelablePromise<LoginResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/auth/refresh',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * List Users
     * List all users with cursor-based pagination.
     * @returns UserListResponse Successful Response
     * @throws ApiError
     */
    public static listUsers({
        cursor,
        limit = 50,
        inviteState,
    }: {
        cursor?: (string | null),
        limit?: number,
        inviteState?: (InviteState | null),
    }): CancelablePromise<UserListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/users',
            query: {
                'cursor': cursor,
                'limit': limit,
                'invite_state': inviteState,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Create User
     * Create a new user and issue an invite token.
     * @returns UserCreatedResponse Successful Response
     * @throws ApiError
     */
    public static createUser({
        requestBody,
    }: {
        requestBody: UserCreateRequest,
    }): CancelablePromise<UserCreatedResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/users',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Get current user
     * Return the authenticated user's profile.
     * @returns CurrentUserResponse Successful Response
     * @throws ApiError
     */
    public static getCurrentUser(): CancelablePromise<CurrentUserResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/users/me',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Change own password
     * Change the authenticated user's password and return a fresh token.
     *
     * A new token is required because the caller's current token still carries the
     * stale ``must_change_password`` claim; returning a re-minted token is what
     * actually clears the rotation gate client-side.
     * @returns LoginResponse Successful Response
     * @throws ApiError
     */
    public static changePassword({
        requestBody,
    }: {
        requestBody: ChangePasswordRequest,
    }): CancelablePromise<LoginResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/users/me:change-password',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Delete User
     * Soft-delete a user.
     * @returns void
     * @throws ApiError
     */
    public static deleteUser({
        userId,
    }: {
        userId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/users/{user_id}',
            path: {
                'user_id': userId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Get User
     * Get a user by ID.
     * @returns UserResponse Successful Response
     * @throws ApiError
     */
    public static getUser({
        userId,
    }: {
        userId: string,
    }): CancelablePromise<UserResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/users/{user_id}',
            path: {
                'user_id': userId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Update User
     * Update a user's profile fields.
     * @returns UserResponse Successful Response
     * @throws ApiError
     */
    public static updateUser({
        userId,
        requestBody,
    }: {
        userId: string,
        requestBody: UserUpdateRequest,
    }): CancelablePromise<UserResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/users/{user_id}',
            path: {
                'user_id': userId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Set User Permissions
     * Set the assigned permissions for a user.
     * @returns UserResponse Successful Response
     * @throws ApiError
     */
    public static setUserPermissions({
        userId,
        requestBody,
    }: {
        userId: string,
        requestBody: SetPermissionsRequest,
    }): CancelablePromise<UserResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/users/{user_id}/permissions',
            path: {
                'user_id': userId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Disable User
     * Disable a user account.
     * @returns void
     * @throws ApiError
     */
    public static disableUser({
        userId,
    }: {
        userId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/users/{user_id}:disable',
            path: {
                'user_id': userId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Enable User
     * Enable a user account.
     * @returns void
     * @throws ApiError
     */
    public static enableUser({
        userId,
    }: {
        userId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/users/{user_id}:enable',
            path: {
                'user_id': userId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Reissue Invite
     * Reissue an invite token for a user.
     * @returns InviteIssuedResponse Successful Response
     * @throws ApiError
     */
    public static reissueInvite({
        userId,
    }: {
        userId: string,
    }): CancelablePromise<InviteIssuedResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/users/{user_id}:reissue-invite',
            path: {
                'user_id': userId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Create first admin (one-time setup)
     * First-run setup: create the first admin user and auto-login.
     *
     * Unauthenticated by design — there is no admin to authenticate as yet. The
     * operation self-closes once any user exists (returns 410 ``setup_already_complete``
     * thereafter), so it is safe to expose only during first boot.
     * @returns LoginResponse Successful Response
     * @throws ApiError
     */
    public static createAdmin({
        requestBody,
    }: {
        requestBody: CreateAdminRequest,
    }): CancelablePromise<LoginResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/users:create-admin',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                410: `Setup already complete — the first admin exists and this endpoint is closed.`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Redeem invite
     * Redeem an invite token, set password, and return a JWT.
     * @returns LoginResponse Successful Response
     * @throws ApiError
     */
    public static redeemInvite({
        requestBody,
    }: {
        requestBody: RedeemInviteRequest,
    }): CancelablePromise<LoginResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/users:redeem-invite',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
}
