/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Token endpoint success response.
 */
export type TokenResponse = {
    access_token: string;
    expires_in: number;
    id_token?: (string | null);
    refresh_token?: (string | null);
    /**
     * Space-delimited effective scopes of the minted access token (RFC 6749 §3.3), computed the way the platform's resolvers enforce them (live scope grants ∩ client ceiling ∩ consent-grant scopes for agent and service-account tokens), so the granted set may be narrower than requested and clients must not assume they got what they asked for. Present on every response whose token carries at least one scope; OMITTED (never the ABNF-invalid empty string) only when the effective set is empty — reachable solely on legs where the client requested no scopes at the token endpoint (the token request carries no scope parameter, and consent fails closed on an empty intersection).
     */
    scope?: (string | null);
    token_type?: string;
};

