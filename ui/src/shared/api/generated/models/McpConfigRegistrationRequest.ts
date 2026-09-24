/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { McpConfigRuntime } from './McpConfigRuntime';
/**
 * Report that an MCP config entry was written for one agent runtime.
 */
export type McpConfigRegistrationRequest = {
    /**
     * The agent runtime whose MCP config entry was written — a closed set; clients map anything unrecognised to `other`.
     */
    runtime: McpConfigRuntime;
};

