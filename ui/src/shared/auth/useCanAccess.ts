/**
 * Whether the signed-in caller holds ANY of `permissions`, or `org:admin`, in
 * their effective permissions (`GET /users/me`, already expanded through the
 * implication map, so `agents:write` carries `agents:read`). Mirrors the
 * server's `required_permissions` check: any one listed permission, or
 * `org:admin`, passes.
 *
 * Surfaces gate a request or an affordance on it so a caller without access
 * gets a quiet state instead of a refused call. A UI gate only: the server
 * still enforces access. While the provider is still loading the user the
 * answer is `false`, so nothing is requested before the permissions are known.
 * Outside an `AuthProvider` (shell chrome in tests) the viewer is unknown and
 * the answer is `true`: the request goes out and the server decides.
 */
import { useOptionalAuth } from '@/shared/auth/AuthContext';
import { ORG_ADMIN } from '@/shared/auth/usePermission';

/** Read the agent roster (`GET /agents`). */
export const AGENTS_READ = 'agents:read';
/** Create, approve, deny and manage agents. */
export const AGENTS_WRITE = 'agents:write';
/** Read the async job queue (`GET /jobs`). */
export const JOBS_READ = 'jobs:read';
/** Cancel jobs and decide execution approvals (`POST /executions/approvals/{id}:decide`). */
export const JOBS_WRITE = 'jobs:write';
/** Read the audit log (`GET /audit`). */
export const AUDIT_READ = 'audit:read';
/** Read every credential (`GET /credentials`). */
export const CREDENTIALS_READ = 'credentials:read';
/** Create, edit, delete and connect credentials, and edit a binding's rules. */
export const CREDENTIALS_WRITE = 'credentials:write';
/** Read the credentials the caller owns (`GET /credentials`, owner-scoped). */
export const OWNER_CREDENTIALS_READ = 'owner:credentials:read';

/**
 * Whether the caller's permissions are known: the provider has loaded the user,
 * or there is no provider (the viewer is unknown and {@link useCanAccess} lets
 * the server decide). A surface uses it to tell "not allowed" apart from "not
 * loaded yet", so it never flashes a no-access state before `/users/me` answers.
 */
export function usePermissionsKnown(): boolean {
	const auth = useOptionalAuth();
	return !auth || auth.user != null;
}

export function useCanAccess(...permissions: string[]): boolean {
	const auth = useOptionalAuth();
	if (!auth) return true;
	if (!auth.user) return false;
	const held = auth.user.permissions ?? [];
	return held.includes(ORG_ADMIN) || permissions.some((p) => held.includes(p));
}
