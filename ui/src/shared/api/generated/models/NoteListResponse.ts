/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { NoteResponse } from './NoteResponse';
/**
 * Cursor-paginated list of notes.
 */
export type NoteListResponse = {
    data: Array<NoteResponse>;
    has_more: boolean;
    next_cursor?: (string | null);
};

