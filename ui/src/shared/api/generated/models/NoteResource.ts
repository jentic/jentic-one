/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { NoteApiReference } from './NoteApiReference';
/**
 * Exactly-one-of resource identifier for a note (request side).
 */
export type NoteResource = {
    /**
     * API to attach the note to; set exactly one resource field.
     */
    api?: (NoteApiReference | null);
    /**
     * ID of the credential to attach the note to; set exactly one resource field.
     */
    credential_id?: (string | null);
    /**
     * ID of the execution to attach the note to; set exactly one resource field.
     */
    execution_id?: (string | null);
    /**
     * ID of the operation to attach the note to; set exactly one resource field.
     */
    operation_id?: (string | null);
};

