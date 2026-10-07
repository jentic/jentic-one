/**
 * Who may bind which credential, as the UI mirrors it. The server enforces the
 * rule (a non-admin binding a credential they did not create gets a 404); the UI
 * only avoids offering a bind that cannot succeed, and avoids reading "missing
 * from my credential list" as "deleted" when the list is owner-scoped.
 *
 * Shared because two modules offer a bind — the agents surfaces and the API
 * hub's "Bind to an agent" — and must agree on who may do it.
 */
import { AGENTS_WRITE, CREDENTIALS_WRITE, ORG_ADMIN, useOptionalCurrentUser } from '@/shared/auth';
import type { Credential } from '@/shared/credentials/api';
import { credentialEditableBy } from '@/shared/credentials/lib/credentialAuthority';

/** The slice of the signed-in user these rules read. `null` = not known yet
 * (still loading, or rendered outside an `AuthProvider`). */
export interface BindViewer {
	id: string;
	permissions?: readonly string[] | null;
}

/** An `org:admin` binds any credential and sees the whole org's list. An unknown
 * viewer is not an admin. */
export function viewerIsOrgAdmin(viewer: BindViewer | null | undefined): boolean {
	return viewer?.permissions?.includes(ORG_ADMIN) ?? false;
}

/**
 * The credentials this viewer may bind: every one for an `org:admin`, otherwise
 * only the ones they created. An unknown viewer gets the list unfiltered — the
 * server still enforces, and hiding everything while `/users/me` loads would
 * misclassify every pick as "needs a new credential". The same owner-or-admin
 * rule decides who may edit a credential ({@link credentialEditableBy}).
 */
export function credentialsBindableBy(
	credentials: Credential[],
	viewer: BindViewer | null | undefined,
): Credential[] {
	if (!viewer || viewerIsOrgAdmin(viewer)) return credentials;
	return credentials.filter((c) => credentialEditableBy(c, viewer));
}

/**
 * Whether to offer a bind at all: the viewer holds `agents:write` (or is an
 * `org:admin`). An unknown viewer (still loading / no `AuthProvider`) is
 * offered it — a UX-only gate, the server stays the source of truth.
 */
export function useCanBindAgents(): boolean {
	return useViewerMayWrite(AGENTS_WRITE);
}

/**
 * Whether to offer creating a credential: the viewer holds `credentials:write`
 * (or is an `org:admin`, which the server accepts in its place). Same UX-only
 * posture as {@link useCanBindAgents}: an unknown viewer is offered it.
 */
export function useCanCreateCredentials(): boolean {
	return useViewerMayWrite(CREDENTIALS_WRITE);
}

/** `permission` or `org:admin` (accepted in its place); an unknown viewer may. */
function useViewerMayWrite(permission: string): boolean {
	const viewer = useOptionalCurrentUser();
	if (!viewer) return true;
	const perms = viewer.permissions ?? [];
	return perms.includes(permission) || perms.includes(ORG_ADMIN);
}
