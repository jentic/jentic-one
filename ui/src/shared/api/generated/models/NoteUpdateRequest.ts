/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { NoteConfidence } from './NoteConfidence';
import type { NoteSource } from './NoteSource';
import type { NoteType } from './NoteType';
/**
 * Payload for updating an existing note (partial).
 */
export type NoteUpdateRequest = {
    /**
     * New text of the note, up to 4000 characters; may be omitted but not null.
     */
    body?: (string | null);
    /**
     * New confidence in the note; null clears it.
     */
    confidence?: (NoteConfidence | null);
    /**
     * ID of an execution the note relates to; null clears it.
     */
    related_execution_id?: (string | null);
    /**
     * New origin of the note; null clears it.
     */
    source?: (NoteSource | null);
    /**
     * New category of the note; null clears it.
     */
    type?: (NoteType | null);
};

