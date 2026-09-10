/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EnabledMethodResponse } from './EnabledMethodResponse';
import type { IdpMethodResponse } from './IdpMethodResponse';
import type { OauthClientDcrMethodResponse } from './OauthClientDcrMethodResponse';
/**
 * The login-picker contract: the sign-in options on the process answering.
 *
 * Scope caveat for split deployments: mount-derived flags (``agent_dcr``,
 * ``service_accounts``) describe **this process only** — ``false`` means "not
 * served here", not "does not exist on the deployment"; a sibling tier may
 * serve it (see ``surfaces``).
 */
export type AuthMethodsResponse = {
    /**
     * Anonymous agent self-registration (RFC 7591, POST at urls.agent_registration); true iff the auth surface is mounted on this process.
     */
    agent_dcr: EnabledMethodResponse;
    idp: IdpMethodResponse;
    /**
     * Local-account login form on the /authorize flow (auth.local_login). The *effective* offer: false whenever an external IdP is enabled, because the IdP always wins and the form is never reachable (no mixed mode). Entry point: urls.authorize.
     */
    local_login: EnabledMethodResponse;
    oauth_client_dcr: OauthClientDcrMethodResponse;
    /**
     * Operator-managed service accounts (jwt-bearer grant at urls.token); true iff the auth surface is mounted on this process.
     */
    service_accounts: EnabledMethodResponse;
};

