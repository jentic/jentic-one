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
export { ProvisioningRequestDialog } from '@/shared/app/rail/ProvisioningRequestDialog';
export type { ProvisioningRequestDialogProps } from '@/shared/app/rail/ProvisioningRequestDialog';
export { AccessRequestDecisionDialog } from '@/shared/app/rail/AccessRequestDecisionDialog';
export type { AccessRequestDecisionDialogProps } from '@/shared/app/rail/AccessRequestDecisionDialog';

// The operations preview/dialog pair is the platform's ONE grammar for showing
// what a set of permission rules grants (effect chips + bounded operation
// preview + full-view dialog). Access-request cards use it at review time and
// the toolkits Access tab uses it on live bindings, so both read identically.
export { OperationsSummary } from '@/shared/app/rail/OperationsSummary';
export { OperationsDialog } from '@/shared/app/rail/OperationsDialog';
export type { OperationsDialogProps } from '@/shared/app/rail/OperationsDialog';
