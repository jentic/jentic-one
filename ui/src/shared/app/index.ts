export { createQueryClient } from '@oss-internal/shared/app/query-client';
export { Layout } from '@oss-internal/shared/app/Layout';
export { ROUTES, ROUTE_PATHS, moduleRoutes } from '@oss-internal/shared/app/routes';
export { navItems, sortedNavItems } from '@oss-internal/shared/app/nav';
export type { NavItem } from '@oss-internal/shared/app/nav';

// The access-request decision dialog is reusable beyond the rail (e.g. the
// Dashboard's pending-requests card), so it's surfaced here for module views to
// consume via the `@/shared/app` barrel rather than a deep rail path.
export { AccessRequestDialog } from '@oss-internal/shared/app/rail/AccessRequestDialog';
export type { AccessRequestDialogProps } from '@oss-internal/shared/app/rail/AccessRequestDialog';
