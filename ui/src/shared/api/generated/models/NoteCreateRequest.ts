/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { NoteConfidence } from './NoteConfidence';
import type { NoteResource } from './NoteResource';
import type { NoteSource } from './NoteSource';
import type { NoteType } from './NoteType';
/**
 * Payload for creating a new note.
 */
export type NoteCreateRequest = {
    /**
     * Text of the note, up to 4000 characters.
     */
    body: string;
    /**
     * Author's confidence in the note; null if not stated.
     */
    confidence?: (NoteConfidence | null);
    /**
     * ID of an execution the note relates to; null if none.
     */
    related_execution_id?: (string | null);
    resource: NoteResource;
    /**
     * Origin of the note (`agent`, `human` or `platform`); null if not stated.
     */
    source?: (NoteSource | null);
    /**
     * Category of the note; null if uncategorised.
     */
    type?: (NoteType | null);
};

