/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The session's new current revision.
 */
export type AmendSessionResponse = {
    revision_id: string;
    /**
     * 2 for the first amend, and so on.
     */
    revision_number: number;
    session_id: string;
};

