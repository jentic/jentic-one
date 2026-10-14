/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Health check response for the admin surface.
 */
export type HealthResponse = {
    /**
     * Next setup step to perform, e.g. `create_admin`; null when setup is complete.
     */
    next_step?: (string | null);
    /**
     * Whether first-run setup is still needed because no user exists yet.
     */
    setup_required: boolean;
    /**
     * Health status of the surface; `ok` when it is serving.
     */
    status: string;
    /**
     * Name of the surface reporting health; always `admin`.
     */
    surface: string;
};

