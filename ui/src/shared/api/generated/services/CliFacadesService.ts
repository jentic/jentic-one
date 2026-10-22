/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CliFacadeListResponse } from '../models/CliFacadeListResponse';
import type { CliProfileListResponse } from '../models/CliProfileListResponse';
import type { CliProfileProvisionResponse } from '../models/CliProfileProvisionResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class CliFacadesService {
    /**
     * List vendor CLI facades
     * The vendor CLI facades this deployment serves, and the address of each.
     *
     * `jentic cf` (and `jentic cli list`) read this to learn where to point the
     * vendor's own CLI, so no agent hard-codes a facade address. Lists only
     * facades that are switched on (`cli_facades.enabled` and the vendor's
     * `enabled`) and implemented by the Broker. Whether the caller holds a
     * credential for the vendor is not checked here: a call without one fails
     * at the facade with the usual Broker problem.
     *
     * Each facade also carries the agent-side wiring from the vendor's CLI
     * profile: the binary and supported versions, the environment `jentic
     * <cli>` sets and strips, and what `jentic run --container` sets and
     * governs, so no CLI hard-codes them.
     * @returns CliFacadeListResponse Successful Response
     * @throws ApiError
     */
    public static listCliFacades(): CancelablePromise<CliFacadeListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/cli-facades',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * List CLI profiles
     * The vendor CLI profiles this Jentic ships.
     *
     * A profile declares a vendor CLI's APIs (with the `jentic connect` key of
     * each), its rule presets (for `jentic connect --preset`) and the hard-deny
     * lists no rule can override. Each API says whether the registry serves it
     * now; `POST /cli-profiles/{profile}:provision` makes them all live.
     * @returns CliProfileListResponse Successful Response
     * @throws ApiError
     */
    public static listCliProfiles(): CancelablePromise<CliProfileListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/cli-profiles',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Provision a CLI profile's APIs
     * Make every API the profile declares exist and be live. Idempotent.
     *
     * A `catalog` API is imported from the public catalog (enqueued; nothing
     * when it is already registered), a `shipped` API is imported from the spec
     * shipped with Jentic and goes live at once, and a `builtin` API needs
     * nothing. A connect for one of the profile's APIs runs the same
     * provisioning on demand, so this is for an operator who wants the APIs
     * in place before any agent connects. Runs whether or not the vendor's
     * facade is enabled (`vendor_enabled`).
     * @returns CliProfileProvisionResponse Successful Response
     * @throws ApiError
     */
    public static provisionCliProfile({
        profile,
    }: {
        /**
         * Profile name, e.g. `github`.
         */
        profile: string,
    }): CancelablePromise<CliProfileProvisionResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/cli-profiles/{profile}:provision',
            path: {
                'profile': profile,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                404: `No shipped CLI profile has this name`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
}
