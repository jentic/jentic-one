/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Opaque poll capability returned by :connect. Optional for the target agent's owner (with credentials:write and agents:write) and org:admin. Deliberately a query parameter: it rides the approval URL a human opens in a browser, where a header cannot be set. It is a short-lived, single-session capability, not a reusable credential, and it only scopes reads/actions on that one connect session.
 */
export type QueryPollToken = (string | null);

