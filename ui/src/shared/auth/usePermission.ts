/**
 * Client-side permission gate. Reads the current user's permission list (from
 * the shared auth context) and answers whether a required permission is held.
 *
 * This hides/disables affordances (nav entries, buttons, tabs) the backend
 * would 403 anyway — a UX nicety, NOT a security boundary. The server remains
 * the source of truth; never rely on this alone to protect data.
 */
import { useAuth, useOptionalCurrentUser } from '@/shared/auth/AuthContext';

export function usePermission(required: string): boolean {
	const { user } = useAuth();
	return user?.permissions?.includes(required) ?? false;
}

/**
 * `usePermission` for shared components that may render outside an
 * `AuthProvider` (the credential create flow and inventory sheet are hosted by
 * many modules). No provider means no known user, so the permission reads as
 * not held and the gated affordance stays hidden.
 */
export function useOptionalPermission(required: string): boolean {
	return useOptionalCurrentUser()?.permissions?.includes(required) ?? false;
}

/** The org-wide admin permission. */
export const ORG_ADMIN = 'org:admin';
