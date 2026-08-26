/**
 * Webhooks module public API surface.
 *
 * Components/pages import from here only — never from `./client` or
 * `@/shared/api` directly (ESLint-enforced layering).
 */
export {
	useWebhookEndpoints,
	useWebhookEndpoint,
	useWebhookDeliveries,
	useWebhookEndpointStats,
	useWebhookEndpointStatsList,
	useWebhookDeliveryAttempts,
	useWebhookEventCatalog,
	useCreateWebhookEndpoint,
	useUpdateWebhookEndpoint,
	useDeleteWebhookEndpoint,
	useRotateWebhookSecret,
	useSendTestEvent,
	useResendDelivery,
	webhookKeys,
	STATS_FANOUT_CAP,
} from '@/modules/webhooks/api/hooks';

export { WebhooksApiError } from '@/modules/webhooks/api/client';
export type { CreateEndpointParams, UpdateEndpointParams } from '@/modules/webhooks/api/client';
export type { WebhookEndpointStatsResult } from '@/modules/webhooks/api/hooks';

export type {
	CreatedEndpoint,
	RotatedSecret,
	WebhookDeliveryEntity,
	WebhookDeliveryAttemptEntity,
	WebhookDeliveryStatus,
	WebhookEndpointEntity,
	WebhookEndpointStats,
} from '@/modules/webhooks/api/types';

export {
	WEBHOOK_EVENT_CATALOG,
	WEBHOOK_EVENT_BY_TYPE,
	WEBHOOK_EVENT_CATEGORY_LABELS,
	groupEventsByNoun,
	eventNoun,
} from '@/modules/webhooks/api/eventCatalog';
export type { WebhookEventTypeInfo } from '@/modules/webhooks/api/eventCatalog';
