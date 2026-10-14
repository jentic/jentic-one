/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { NoteCreateRequest } from '../models/NoteCreateRequest';
import type { NoteUpdateRequest } from '../models/NoteUpdateRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class NotesService {
    /**
     * List Notes
     * List notes with optional filters and cursor pagination.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static listNotes({
        cursor,
        limit = 50,
        api,
        operationId,
        executionId,
        credentialId,
        type,
        createdBy,
    }: {
        /**
         * Opaque cursor from a previous page's `next_cursor`; omit for the first page.
         */
        cursor?: (string | null),
        /**
         * Maximum number of notes to return (1-200).
         */
        limit?: number,
        /**
         * Only return notes attached to APIs matching `vendor[:name[:version]]`.
         */
        api?: (string | null),
        /**
         * Only return notes attached to this operation.
         */
        operationId?: (string | null),
        /**
         * Only return notes attached to this execution.
         */
        executionId?: (string | null),
        /**
         * Only return notes attached to this credential.
         */
        credentialId?: (string | null),
        /**
         * Only return notes of this type.
         */
        type?: (string | null),
        /**
         * Only return notes created by this actor ID.
         */
        createdBy?: (string | null),
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/notes',
            query: {
                'cursor': cursor,
                'limit': limit,
                'api': api,
                'operation_id': operationId,
                'execution_id': executionId,
                'credential_id': credentialId,
                'type': type,
                'created_by': createdBy,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Create Note
     * Create a new note attached to a registry resource.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static createNote({
        requestBody,
    }: {
        /**
         * Payload for creating a new note.
         */
        requestBody: NoteCreateRequest,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/notes',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Delete Note
     * Delete a note. Supports optimistic concurrency via If-Match.
     * @returns void
     * @throws ApiError
     */
    public static deleteNote({
        noteId,
        ifMatch,
    }: {
        /**
         * ID of the note.
         */
        noteId: string,
        /**
         * Expected note revision (the `ETag` value); the request fails with 412 if the note has changed.
         */
        ifMatch?: (string | null),
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/notes/{note_id}',
            path: {
                'note_id': noteId,
            },
            headers: {
                'if-match': ifMatch,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Get Note
     * Retrieve a single note by ID.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getNote({
        noteId,
    }: {
        /**
         * ID of the note.
         */
        noteId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/notes/{note_id}',
            path: {
                'note_id': noteId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Update Note
     * Update a note (partial). Supports optimistic concurrency via If-Match.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static updateNote({
        noteId,
        requestBody,
        ifMatch,
    }: {
        /**
         * ID of the note.
         */
        noteId: string,
        /**
         * Payload for updating an existing note (partial).
         */
        requestBody: NoteUpdateRequest,
        /**
         * Expected note revision (the `ETag` value); the request fails with 412 if the note has changed.
         */
        ifMatch?: (string | null),
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/notes/{note_id}',
            path: {
                'note_id': noteId,
            },
            headers: {
                'if-match': ifMatch,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
}
