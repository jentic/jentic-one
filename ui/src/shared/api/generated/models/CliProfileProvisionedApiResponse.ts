/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * What provisioning did for one API.
 */
export type CliProfileProvisionedApiResponse = {
    /**
     * The api_id.
     */
    api_id: string;
    /**
     * Why, for `failed` and the other non-live states.
     */
    detail?: (string | null);
    /**
     * The enqueued import job, for `import_queued`.
     */
    job_id?: (string | null);
    /**
     * Registry API name.
     */
    name: string;
    /**
     * The API's role.
     */
    role: CliProfileProvisionedApiResponse.role;
    /**
     * Where the API comes from.
     */
    source: string;
    /**
     * `live`: already served, nothing done; `imported`: the shipped spec was imported and is live; `import_queued`: a catalog import was enqueued (see `job_id`); `builtin`: served by the Broker, nothing to import; `held_for_review`: imported as a draft because it changes the API's hosts; `not_in_catalog`: the catalog has no such entry; `failed`: see `detail`.
     */
    status: CliProfileProvisionedApiResponse.status;
    /**
     * Registry vendor.
     */
    vendor: string;
    /**
     * API version, when known.
     */
    version?: (string | null);
};
export namespace CliProfileProvisionedApiResponse {
    /**
     * The API's role.
     */
    export enum role {
        REST = 'rest',
        GRAPHQL = 'graphql',
        GIT = 'git',
        UPLOADS = 'uploads',
    }
    /**
     * `live`: already served, nothing done; `imported`: the shipped spec was imported and is live; `import_queued`: a catalog import was enqueued (see `job_id`); `builtin`: served by the Broker, nothing to import; `held_for_review`: imported as a draft because it changes the API's hosts; `not_in_catalog`: the catalog has no such entry; `failed`: see `detail`.
     */
    export enum status {
        LIVE = 'live',
        IMPORTED = 'imported',
        IMPORT_QUEUED = 'import_queued',
        BUILTIN = 'builtin',
        HELD_FOR_REVIEW = 'held_for_review',
        NOT_IN_CATALOG = 'not_in_catalog',
        FAILED = 'failed',
    }
}

