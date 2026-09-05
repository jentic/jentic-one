/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EnabledMethodResponse } from './EnabledMethodResponse';
import type { IdpMethodResponse } from './IdpMethodResponse';
import type { OauthClientDcrMethodResponse } from './OauthClientDcrMethodResponse';
/**
 * The login-picker contract: exactly the sign-in options this deployment supports.
 */
export type AuthMethodsResponse = {
    /**
     * Anonymous agent self-registration (POST /register, RFC 7591); available whenever the auth surface is mounted.
     */
    agent_dcr: EnabledMethodResponse;
    idp: IdpMethodResponse;
    /**
     * Local-account login form on the /authorize flow (no external IdP). Currently always false; wired to auth.local_login when #1276 ships.
     */
    local_login: EnabledMethodResponse;
    oauth_client_dcr: OauthClientDcrMethodResponse;
    /**
     * Operator-managed service accounts (JWT-bearer grant); available whenever the auth surface is mounted.
     */
    service_accounts: EnabledMethodResponse;
};

