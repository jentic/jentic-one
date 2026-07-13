/**
 * Monitor module routes. The path is RELATIVE to the `/app` shell, so this
 * mounts at `/app/monitor`. Registered additively into `@/shared/app/routes.ts`.
 *
 * Monitor is a single page; all view state (active tab, open detail sheet,
 * per-tab filters, live toggle) lives in URL search params rather than nested
 * route segments — see `@/modules/monitor/lib/links`. e.g.
 * `/app/monitor?tab=executions&trace_id=…` opens the trace detail sheet, and
 * `/app/monitor?tab=jobs&job_id=…` opens the job detail sheet.
 */
import type { RouteObject } from 'react-router-dom';
import MonitorPage from '@/modules/monitor/pages/MonitorPage';

export const monitorRoutes: RouteObject[] = [{ path: 'monitor', element: <MonitorPage /> }];
