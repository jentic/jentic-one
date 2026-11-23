/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiReferenceResponse } from './ApiReferenceResponse';
import type { ApiRevisionLinksResponse } from './ApiRevisionLinksResponse';
import type { ApiRevisionSourceInlineResponse } from './ApiRevisionSourceInlineResponse';
import type { ApiRevisionSourceUrlResponse } from './ApiRevisionSourceUrlResponse';
/**
 * Full API revision response.
 */
export type ApiRevisionResponse = {
    _links: ApiRevisionLinksResponse;
    api: ApiReferenceResponse;
    archived_at: (string | null);
    created_at: string;
    is_current: boolean;
    operation_count: number;
    origin?: (string | null);
    promoted_at: (string | null);
    revision_id: string;
    source: ((ApiRevisionSourceUrlResponse | ApiRevisionSourceInlineResponse) | null);
    spec_digest: (string | null);
    state: string;
    submitted_by: (string | null);
};

