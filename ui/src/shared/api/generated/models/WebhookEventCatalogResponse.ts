/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WebhookEventCatalogEntry } from './WebhookEventCatalogEntry';
/**
 * The canonical set of subscribable event types.
 *
 * Served so the UI's event picker cannot drift from the backend
 * ``EventType.ALL`` minus the never-relayed set. The synthetic ``webhook.test``
 * type is deliberately excluded — it is not subscribable.
 */
export type WebhookEventCatalogResponse = {
    data: Array<WebhookEventCatalogEntry>;
};

