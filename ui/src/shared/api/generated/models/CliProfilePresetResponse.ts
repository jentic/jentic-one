/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CliProfilePresetParamResponse } from './CliProfilePresetParamResponse';
/**
 * A named rule preset, expanded into concrete rules at connect time.
 */
export type CliProfilePresetResponse = {
    /**
     * What the preset grants.
     */
    description: string;
    /**
     * Preset name, for `jentic connect --preset`.
     */
    name: string;
    /**
     * The preset's parameters.
     */
    params: Array<CliProfilePresetParamResponse>;
    /**
     * The API roles the preset has rules for.
     */
    roles: Array<'rest' | 'graphql' | 'git' | 'uploads'>;
};

