/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request body for minting a CLI invocation token.
 */
export type CliInvocationCreateRequest = {
    /**
     * The command's subcommand path as reported by the agent, e.g. `pr create`; never its arguments (words starting with `-` are rejected). Display-only.
     */
    command_title?: (string | null);
    /**
     * Name of the credential to use when the agent has several for this vendor (the wrapper's `JENTIC_CREDENTIAL`). The Broker applies it to every call the token makes, exactly as the `Jentic-Credential-Name` header; omit it to let the Broker select the credential as for any call. Not checked at mint: a name that matches none of the agent's credentials fails each call instead.
     */
    credential_name?: (string | null);
    /**
     * Vendor whose CLI facade the token is for: `github`, `cloudflare` or `aws`.
     */
    vendor: string;
};

