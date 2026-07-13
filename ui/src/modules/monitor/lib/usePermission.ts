/**
 * Permission gate for Monitor's privileged actions.
 *
 * Reads the current user's permission list (from the shared auth context) and
 * answers whether a required permission is held. Cancel-job and the audit lens
 * are org:admin-only on the backend; the UI hides/disables those affordances
 * for non-admins rather than letting the call 403.
 */
import { useAuth } from '@/shared/auth';

export function usePermission(required: string): boolean {
	const { user } = useAuth();
	return user?.permissions?.includes(required) ?? false;
}

export const ORG_ADMIN = 'org:admin';
