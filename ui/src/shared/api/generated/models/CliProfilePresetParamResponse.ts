/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A parameter a preset is filled in with.
 */
export type CliProfilePresetParamResponse = {
    /**
     * Value used when it is not given.
     */
    default?: (string | null);
    /**
     * What the parameter means.
     */
    description: string;
    /**
     * `repo_list`: comma-separated `<owner>/<repo>` globs; `ref_glob`: a branch glob below `refs/heads/`; `flag`: `true` or `false`.
     */
    kind: CliProfilePresetParamResponse.kind;
    /**
     * Parameter name, for `--preset-param <name>=…`.
     */
    name: string;
};
export namespace CliProfilePresetParamResponse {
    /**
     * `repo_list`: comma-separated `<owner>/<repo>` globs; `ref_glob`: a branch glob below `refs/heads/`; `flag`: `true` or `false`.
     */
    export enum kind {
        REPO_LIST = 'repo_list',
        REF_GLOB = 'ref_glob',
        FLAG = 'flag',
    }
}

