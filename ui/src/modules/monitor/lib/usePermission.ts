/**
 * Monitor's permission gate — now a thin re-export of the shell-wide hook in
 * `shared/auth`. Kept so Monitor's existing imports (`@/modules/monitor/lib/
 * usePermission`) stay valid; new call sites should import from `@/shared/auth`.
 */
export { usePermission, ORG_ADMIN } from '@/shared/auth/usePermission';
