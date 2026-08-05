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
     * The latest release version reported to this backend (by `jenticctl update`), without a leading 'v'. Null when nothing has been reported yet or this surface has no admin database.
     */
    latest?: (string | null);
    /**
     * True when `latest` is a newer release than `current`. Matches the verdict `jenticctl update` would print.
     */
    update_available: boolean;
};

