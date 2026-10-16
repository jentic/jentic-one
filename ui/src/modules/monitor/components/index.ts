/**
 * Monitor component-tier barrel.
 *
 * The curated, intentionally reusable Monitor views — currently the usage
 * charts, for any screen (inside the module or a downstream host built on the
 * module seams) that renders `useUsageStats` data. Pair with the usage-window
 * plumbing from `@/modules/monitor/api` (`useUsageWindow`, `USAGE_TOP_LIMIT`)
 * so every chart screen shares one query key per window. Everything else in
 * this folder is module-internal.
 *
 * Files inside this folder import each other directly, never through this
 * barrel (that would create an import cycle).
 */
export { UsageCharts } from '@/modules/monitor/components/UsageCharts';
export type { UsageChartsProps } from '@/modules/monitor/components/UsageCharts';
