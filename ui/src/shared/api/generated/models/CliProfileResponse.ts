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
     * `per_api`: each API is connected with its own credential.
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
     * `per_api`: each API is connected with its own credential.
     */
    export enum credential_mode {
        PER_API = 'per_api',
        PROFILE_SCOPED = 'profile_scoped',
    }
}

