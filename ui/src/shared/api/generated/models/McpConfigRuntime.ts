/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Closed-enum tag naming the agent runtime an MCP config entry targets.
 *
 * Attached only to ``mcp_config_registered`` — one event per runtime whose
 * config file/entry ``jentic setup``/``jentic skill init`` writes. A closed
 * set (never the raw runtime string) so the config-written → first-session →
 * first-execute funnel stays property-free on the wire.
 */
export enum McpConfigRuntime {
    CLAUDE_DESKTOP = 'claude_desktop',
    CLAUDE_CODE = 'claude_code',
    CURSOR = 'cursor',
    CODEX = 'codex',
    OTHER = 'other',
}
