/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { TagDescription } from './TagDescription';
/**
 * Contextual information about the API an operation belongs to.
 *
 * ``(vendor, name, version)`` is the canonical API reference — the same
 * identity the broker matches credential scopes, revision pins and execution
 * records on. ``display_name`` is a human-readable label only and must never
 * be used as an identifier.
 */
export type ApiContext = {
    /**
     * The API's description from the spec.
     */
    description?: (string | null);
    /**
     * Human-readable label for the API; never an identifier.
     */
    display_name?: (string | null);
    /**
     * Name part of the canonical API reference.
     */
    name: string;
    /**
     * Descriptions of the tags this operation is grouped under.
     */
    tag_descriptions?: Array<TagDescription>;
    /**
     * Vendor part of the canonical API reference.
     */
    vendor: string;
    /**
     * Version part of the canonical API reference.
     */
    version: string;
};

