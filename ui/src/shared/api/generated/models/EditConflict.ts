/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A part of an edit request the binding no longer fits.
 */
export type EditConflict = {
    /**
     * `missing_rule`: a rule the request removes, or adds before, is gone.
     */
    kind: string;
    /**
     * The missing rule.
     */
    rule_id: string;
};

