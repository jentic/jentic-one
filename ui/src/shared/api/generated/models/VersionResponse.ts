/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The running app version and the latest release known to this backend.
 */
export type VersionResponse = {
    /**
     * The version of jentic-one currently running on this backend.
     */
    current: string;
    /**
     * The latest published release of jentic-one, without a leading 'v'. Null when the backend can't determine it (update check disabled, air-gapped, a remote backend, or GitHub was unreachable).
     */
    latest?: (string | null);
    /**
     * True when `latest` is a newer release than `current`. Matches the verdict `jenticctl update` would print.
     */
    update_available: boolean;
};

