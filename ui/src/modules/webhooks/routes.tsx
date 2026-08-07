/**
 * Webhooks module routes. Paths are RELATIVE to the `/app` shell, so these
 * mount at `/app/webhooks` and `/app/webhooks/:webhookId`. Registered
 * additively into `@/shared/app/routes.ts`.
 */
import type { RouteObject } from 'react-router';
import WebhooksPage from '@/modules/webhooks/pages/WebhooksPage';
import WebhookDetailPage from '@/modules/webhooks/pages/WebhookDetailPage';

export const webhooksRoutes: RouteObject[] = [
	{ path: 'webhooks', element: <WebhooksPage /> },
	{ path: 'webhooks/:webhookId', element: <WebhookDetailPage /> },
];
