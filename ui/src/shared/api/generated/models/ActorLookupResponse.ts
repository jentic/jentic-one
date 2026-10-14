/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ActorLookupEntryResponse } from './ActorLookupEntryResponse';
/**
 * Actors resolved by id; ids that match no actor are omitted.
 */
export type ActorLookupResponse = {
    /**
     * The resolved actors; ids that match no visible actor are omitted.
     */
    data: Array<ActorLookupEntryResponse>;
};

