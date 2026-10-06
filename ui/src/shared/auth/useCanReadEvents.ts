/**
 * Whether the signed-in caller may read the platform event feed (`/events`,
 * `/events/stream`): `events:read` or `org:admin` in their effective
 * permissions (`GET /users/me`). Surfaces that read events gate their requests
 * on this so a caller without access gets a clear state instead of a stream
 * that is refused over and over. Loading and no-provider behave as
 * {@link useCanAccess}.
 */
import { useCanAccess } from '@/shared/auth/useCanAccess';

/** The permission that grants reads of the event feed. */
export const EVENTS_READ = 'events:read';

export function useCanReadEvents(): boolean {
	return useCanAccess(EVENTS_READ);
}
