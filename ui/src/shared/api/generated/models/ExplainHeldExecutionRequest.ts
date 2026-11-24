/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The filing agent's explanation of why it made a held call.
 */
export type ExplainHeldExecutionRequest = {
    /**
     * One or two sentences on why the call is needed, shown to the reviewer as the agent's own claim. Control and text-direction characters are removed and the text is cut to 1024 characters.
     */
    reason: string;
};

