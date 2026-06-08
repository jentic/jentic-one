/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type CredentialCreate = {
    label: string;
    value?: string;
    description?: (string | null);
    identity?: (string | null);
    api_id?: (string | null);
    server_variables?: (Record<string, string> | null);
    /**
     * How this credential maps to the upstream API's authentication scheme. The broker uses this to find the right security scheme in the spec — it resolves by type, not by the bespoke scheme name in the overlay.
     *
     * | Value | Injects as | When to use |
     * |---|---|---|
     * | `bearer` | `Authorization: Bearer {value}` | REST APIs, OAuth access tokens, JWTs. GitHub REST API, Deepgram, Slack, etc. |
     * | `basic` | `Authorization: Basic base64({identity??'token'}:{value})` | HTTP Basic auth, git-over-HTTPS. Set `identity` to the username; omit for GitHub PATs (any username accepted). |
     * | `apiKey` | Custom header or query param `= {value}` | API key in a named header (X-API-Key, Api-Key, X-Auth-Key, etc.). For **compound** schemes (e.g. Discourse Api-Key + Api-Username) where the overlay uses canonical `Secret`/`Identity` scheme names, set `identity` to the username/account — a single credential covers both headers. |
     * | `none` | *(nothing injected)* | No-auth APIs where the credential exists only to carry `server_variables` for routing. |
     * | `oauth2` | `Authorization: Bearer {value}` | OAuth access token already obtained by the caller (no refresh handling). |
     * | `pipedream_oauth` | *(reserved)* | Set by Pipedream sync — do not assign manually. |
     * | `JenticApiKey` | *(reserved)* | Internal jentic-mini admin key — do not assign manually. |
     */
    auth_type?: ('bearer' | 'basic' | 'apiKey' | 'none' | 'oauth2' | 'pipedream_oauth' | 'JenticApiKey' | null);
    /**
     * Self-describing injection rule. When set, the broker injects the credential directly from this blob without looking up the API spec or overlay at runtime. Format: {"in": "header", "name": "Authorization", "prefix": "Bearer "} or {"in": "header", "name": "X-Api-Key"}. Supports encode=base64 for Basic auth: {"in": "header", "name": "Authorization", "prefix": "Basic ", "encode": "base64"}. For compound schemes: {"secret": {"in": "header", ...}, "identity": {"in": "header", ...}}.
     */
    scheme?: (Record<string, any> | null);
    /**
     * Hostnames or host+path patterns this credential should be injected into. Each entry is stored as (host, path_prefix) in credential_routes. Example: ["github.com", "api.github.com"].
     */
    routes?: (Array<string> | null);
};

