/**
 * Client-side permission gate. Reads the current user's permission list (from
 * the shared auth context) and answers whether a required permission is held.
 *
 * This hides/disables affordances (nav entries, buttons, tabs) the backend
 * would 403 anyway — a UX nicety, NOT a security boundary. The server remains
 * the source of truth; never rely on this alone to protect data.
 */
import { useAuth } from '@/shared/auth/AuthContext';

export function usePermission(required: string): boolean {
	const { user } = useAuth();
	return user?.permissions?.includes(required) ?? false;
}

/** The org-wide admin permission. */
export const ORG_ADMIN = 'org:admin';
