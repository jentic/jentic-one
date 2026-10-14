/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * HAL-style links for an event.
 */
export type EventLinks = {
    /**
     * Reserved link to a follow-up action; currently always null.
     */
    action?: (string | null);
    /**
     * URL of the related execution record; null if none.
     */
    execution?: (string | null);
    /**
     * URL of the related job; null if none.
     */
    job?: (string | null);
    /**
     * URL of this event.
     */
    self: string;
};

