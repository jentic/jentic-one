/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SecuritySchemeFlowResponse } from './SecuritySchemeFlowResponse';
/**
 * Full security scheme detail for a revision.
 */
export type SecuritySchemeResponse = {
    /**
     * Hint for the bearer token format; null if not declared.
     */
    bearer_format?: (string | null);
    /**
     * Description of the scheme from the spec; null if none.
     */
    description?: (string | null);
    /**
     * OAuth2 flows declared by the scheme; empty for other types.
     */
    flows?: Array<SecuritySchemeFlowResponse>;
    /**
     * Where an API key is sent (`header`, `query` or `cookie`); null for other types.
     */
    in_location?: (string | null);
    /**
     * Name of the security scheme as declared in the spec.
     */
    name: string;
    /**
     * OpenID Connect discovery URL; null unless `type` is `openIdConnect`.
     */
    open_id_connect_url?: (string | null);
    /**
     * Name of the header, query or cookie parameter carrying the API key.
     */
    param_name?: (string | null);
    /**
     * HTTP authentication scheme (for example `bearer`); null unless `type` is `http`.
     */
    scheme?: (string | null);
    /**
     * Security scheme type (for example `apiKey`, `http`, `oauth2` or `openIdConnect`).
     */
    type: string;
};

