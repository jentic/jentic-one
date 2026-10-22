/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CliProfileApiResponse } from './CliProfileApiResponse';
import type { CliProfileHardDenyResponse } from './CliProfileHardDenyResponse';
import type { CliProfilePresetResponse } from './CliProfilePresetResponse';
/**
 * One shipped CLI profile.
 */
export type CliProfileResponse = {
    /**
     * The APIs the profile declares.
     */
    apis: Array<CliProfileApiResponse>;
    /**
     * The vendor CLI binary.
     */
    cli: string;
    /**
     * With `profile_scoped`: the `jentic connect <vendor>` key whose one connect covers every API of the profile. Null with `per_api`.
     */
    connect?: (string | null);
    /**
     * `per_api`: each API is connected with its own credential. `profile_scoped`: `jentic connect <connect>` creates one credential covering exactly the profile's APIs, each binding rule applying to the API of its role.
     */
    credential_mode: CliProfileResponse.credential_mode;
    /**
     * How the profile is named.
     */
    display_name: string;
    /**
     * Lists no rule can override, kept in code.
     */
    hard_deny: Array<CliProfileHardDenyResponse>;
    /**
     * Rule presets.
     */
    presets: Array<CliProfilePresetResponse>;
    /**
     * Profile name.
     */
    profile: string;
    /**
     * CLI versions the profile is written for.
     */
    supported_range?: (string | null);
    /**
     * CLI facade vendor (`cli_facades.vendors` key).
     */
    vendor: string;
    /**
     * Whether the vendor's facade is switched on in this deployment.
     */
    vendor_enabled: boolean;
};
export namespace CliProfileResponse {
    /**
     * `per_api`: each API is connected with its own credential. `profile_scoped`: `jentic connect <connect>` creates one credential covering exactly the profile's APIs, each binding rule applying to the API of its role.
     */
    export enum credential_mode {
        PER_API = 'per_api',
        PROFILE_SCOPED = 'profile_scoped',
    }
}

