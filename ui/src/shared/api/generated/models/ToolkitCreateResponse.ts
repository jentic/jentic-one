/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BindingWarningSchema } from './BindingWarningSchema';
import type { ToolkitResponse } from './ToolkitResponse';
/**
 * Create response — toolkit + bind-time warnings (no key is issued).
 */
export type ToolkitCreateResponse = {
    toolkit: ToolkitResponse;
    /**
     * Non-fatal signals about the create — e.g. inline-bound credentials that landed with zero permission rules (broker denies by default).
     */
    warnings?: Array<BindingWarningSchema>;
};

