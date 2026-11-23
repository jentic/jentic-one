/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Translated security scheme for consumer presentation.
 */
export type AuthInstruction = {
    /**
     * Hint for the bearer token's format, for `scheme=bearer`.
     */
    bearer_format?: (string | null);
    /**
     * The OAuth 2 flows the scheme declares, for `type=oauth2`.
     */
    flows?: null;
    /**
     * Where an API key travels, for `type=apiKey`: `header`, `query` or `cookie`.
     */
    in_location?: (string | null);
    /**
     * Discovery document URL, for `type=openIdConnect`.
     */
    open_id_connect_url?: (string | null);
    /**
     * Name of the header, query or cookie parameter carrying an API key.
     */
    param_name?: (string | null);
    /**
     * HTTP authentication scheme, for `type=http`.
     */
    scheme?: (string | null);
    /**
     * Security scheme type, e.g. `http`, `apiKey`, `oauth2` or `openIdConnect`.
     */
    type: string;
};

