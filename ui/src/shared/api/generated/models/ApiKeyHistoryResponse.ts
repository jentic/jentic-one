/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiKeyHistoryEntryResponse } from './ApiKeyHistoryEntryResponse';
/**
 * Audit trail of API key operations.
 */
export type ApiKeyHistoryResponse = {
    /**
     * API key events, newest first.
     */
    data: Array<ApiKeyHistoryEntryResponse>;
};

