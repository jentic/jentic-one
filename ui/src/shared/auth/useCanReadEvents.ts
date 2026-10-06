/**
 * Whether the signed-in caller may read the platform event feed (`/events`,
 * `/events/stream`): `events:read` or `org:admin` in their effective
 * permissions (`GET /users/me`). Surfaces that read events gate their requests
 * on this so a caller without access gets a clear state instead of a stream
 * that is refused over and over.
 *
 * A UX gate, not a security boundary — the server still enforces. While the
 * provider is still loading the user the answer is `false`, so nothing is
 * requested before the permissions are known. Outside an `AuthProvider` (shell
 * chrome in tests) the viewer is unknown and the answer is `true`: the request
 * goes out and the server decides.
 */
import { useOptionalAuth } from '@/shared/auth/AuthContext';
import { ORG_ADMIN } from '@/shared/auth/usePermission';

/** The permission that grants reads of the event feed. */
export const EVENTS_READ = 'events:read';

export function useCanReadEvents(): boolean {
	const auth = useOptionalAuth();
	if (!auth) return true;
	if (!auth.user) return false;
	const permissions = auth.user.permissions ?? [];
	return permissions.includes(EVENTS_READ) || permissions.includes(ORG_ADMIN);
}
