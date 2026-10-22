/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * How `jentic <cli>` wires the vendor CLI on the agent's machine.
 */
export type CliFacadeWrapperWiringResponse = {
    /**
     * Variables to set for the CLI. A value is a literal or a placeholder the wrapper fills in: `{address}` (the facade address), `{address_host}` (its hostname) or `{token}` (the command's invocation token).
     */
    env_set: Record<string, string>;
    /**
     * Credential variables the CLI could authenticate with instead of the invocation token; removed from its environment.
     */
    env_strip: Array<string>;
};

