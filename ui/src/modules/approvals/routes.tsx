/**
 * Approvals module routes. Paths are RELATIVE to the `/app` shell:
 *   `approvals`     → `/app/approvals`   — pending-approval list
 *   `approvals/:id` → `/app/approvals/:id` — approval detail + decide form
 *
 * Registered additively into `@/shared/app/routes.ts`.
 */
import type { RouteObject } from 'react-router';
import ApprovalsPage from '@/modules/approvals/pages/ApprovalsPage';
import ApprovalDetailPage from '@/modules/approvals/pages/ApprovalDetailPage';

export const approvalsRoutes: RouteObject[] = [
	{ path: 'approvals', element: <ApprovalsPage /> },
	{ path: 'approvals/:id', element: <ApprovalDetailPage /> },
];
