/**
 * Webhooks module routes. Path is RELATIVE to the `/app` shell, so this mounts at
 * `/app/webhooks`. Registered additively into `@/shared/app/routes.ts`.
 *
 * Note the deliberate namespace overlap: the backend owns the API-side
 * `/webhooks/*` prefixes (`/webhooks/endpoints`, `/webhooks/deliveries/{id}`) at the
 * site root, while this route lives under the SPA's `/app` basename. They cannot
 * collide precisely because the SPA is confined to `/app` — see the doc comment
 * in `shared/app/routes.ts`.
 */
import type { RouteObject } from 'react-router';
import WebhooksPage from '@/modules/webhooks/pages/WebhooksPage';

export const webhooksRoutes: RouteObject[] = [{ path: 'webhooks', element: <WebhooksPage /> }];
