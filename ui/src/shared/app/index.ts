export { createQueryClient } from '@/shared/app/query-client';
export { Layout } from '@/shared/app/Layout';
export { ROUTES, ROUTE_PATHS, moduleRoutes } from '@/shared/app/routes';
export { navItems, sortedNavItems } from '@/shared/app/nav';
export type { NavItem } from '@/shared/app/nav';

// The access-request decision dialog is reusable beyond the rail (e.g. the
// Dashboard's pending-requests card), so it's surfaced here for module views to
// consume via the `@/shared/app` barrel rather than a deep rail path.
export { AccessRequestDialog } from '@/shared/app/rail/AccessRequestDialog';
export type { AccessRequestDialogProps } from '@/shared/app/rail/AccessRequestDialog';
