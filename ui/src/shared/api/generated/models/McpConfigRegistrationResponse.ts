/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { McpConfigRuntime } from './McpConfigRuntime';
/**
 * Acknowledgement of a config-registration report.
 */
export type McpConfigRegistrationResponse = {
    /**
     * Whether the report was recorded as an event. `false` means it was accepted but throttled (an identical (actor, runtime) report was already recorded within the last 24h).
     */
    recorded: boolean;
    /**
     * The runtime the report was recorded for.
     */
    runtime: McpConfigRuntime;
};

