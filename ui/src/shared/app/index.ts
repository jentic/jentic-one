export { createQueryClient } from '@/shared/app/query-client';
export { Layout } from '@/shared/app/Layout';
export { ROUTES, ROUTE_PATHS, moduleRoutes } from '@/shared/app/routes';
export { navItems, sortedNavItems } from '@/shared/app/nav';
export type { NavItem } from '@/shared/app/nav';

// The operations preview/dialog pair is the platform's ONE grammar for showing
// what a set of permission rules grants (effect chips + bounded operation
// preview + full-view dialog). The agent console's Access tab uses it on live
// bindings.
export { OperationsSummary } from '@/shared/app/rail/OperationsSummary';
export { OperationsDialog } from '@/shared/app/rail/OperationsDialog';
export type { OperationsDialogProps } from '@/shared/app/rail/OperationsDialog';
