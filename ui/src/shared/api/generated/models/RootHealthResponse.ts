/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Liveness body of the canonical root ``GET /health`` probe.
 */
export type RootHealthResponse = {
    /**
     * Why the deployment is not entitled. Present only on the `not_entitled` body.
     */
    reason?: (string | null);
    /**
     * `ok` while the process is serving requests. A metered marketplace deployment whose entitlement lapsed answers `not_entitled` (still HTTP 200, so orchestrators don't restart it).
     */
    status: string;
    /**
     * Running Jentic One release. Absent on the `not_entitled` body.
     */
    version?: (string | null);
};

