/**
 * Client-side permission gate. Reads the current user's permission list (from
 * the shared auth context) and answers whether a required permission is held.
 *
 * This hides/disables affordances (nav entries, buttons, tabs) the backend
 * would 403 anyway — a UX nicety, NOT a security boundary. The server remains
 * the source of truth; never rely on this alone to protect data.
 *
 * Safe to call outside an `AuthProvider` — returns `false` rather than
 * throwing, so components that gate optional affordances on a permission
 * (e.g. an admin-only toggle inside a shared dialog) can be rendered in
 * unwrapped test setups without pulling the whole auth machinery in.
 */
import { useContext } from 'react';
import { AuthContext } from '@/shared/auth/AuthContext';

export function usePermission(required: string): boolean {
	const ctx = useContext(AuthContext);
	return ctx?.user?.permissions?.includes(required) ?? false;
}

/** The org-wide admin permission. */
export const ORG_ADMIN = 'org:admin';
