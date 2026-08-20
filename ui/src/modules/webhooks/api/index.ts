/**
 * Webhooks module public API surface.
 *
 * Components/pages import from here only — never from `./client` or
 * `@/shared/api` directly (ESLint-enforced layering).
 */
export {
	useWebhookEndpoints,
	useWebhookDeliveries,
	useCreateWebhookEndpoint,
	useUpdateWebhookEndpoint,
	useDeleteWebhookEndpoint,
	useRotateWebhookSecret,
	useSendTestEvent,
	useResendDelivery,
	webhookKeys,
} from '@/modules/webhooks/api/hooks';

export { WebhooksApiError } from '@/modules/webhooks/api/client';
export type { CreateEndpointParams, UpdateEndpointParams } from '@/modules/webhooks/api/client';

export type {
	CreatedEndpoint,
	RotatedSecret,
	WebhookDeliveryEntity,
	WebhookDeliveryStatus,
	WebhookEndpointEntity,
} from '@/modules/webhooks/api/types';

export {
	WEBHOOK_EVENT_CATALOG,
	WEBHOOK_EVENT_BY_TYPE,
	WEBHOOK_EVENT_CATEGORY_LABELS,
} from '@/modules/webhooks/api/eventCatalog';
export type {
	WebhookEventTypeInfo,
	WebhookEventCategory,
} from '@/modules/webhooks/api/eventCatalog';
