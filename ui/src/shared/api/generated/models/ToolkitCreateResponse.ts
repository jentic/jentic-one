/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BindingWarningSchema } from './BindingWarningSchema';
import type { ToolkitResponse } from './ToolkitResponse';
/**
 * Create response: toolkit + api_key shown once.
 */
export type ToolkitCreateResponse = {
    api_key: string;
    toolkit: ToolkitResponse;
    /**
     * Non-fatal signals about the create — e.g. inline-bound credentials that landed with zero permission rules (broker denies by default).
     */
    warnings?: Array<BindingWarningSchema>;
};

