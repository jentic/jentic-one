/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { NoteConfidence } from './NoteConfidence';
import type { NoteLinksResponse } from './NoteLinksResponse';
import type { NoteResourceResponse } from './NoteResourceResponse';
import type { NoteSource } from './NoteSource';
import type { NoteType } from './NoteType';
/**
 * Full note resource response.
 */
export type NoteResponse = {
    _links: NoteLinksResponse;
    body: string;
    confidence: (NoteConfidence | null);
    confidence_source: string;
    created_at: string;
    created_by: string;
    note_id: string;
    related_execution_id: (string | null);
    resource: NoteResourceResponse;
    revision: number;
    source: (NoteSource | null);
    type: (NoteType | null);
    updated_at: string;
};

