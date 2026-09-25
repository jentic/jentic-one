/**
 * Who may bind which credential, as the UI mirrors it. The server enforces the
 * rule (a non-admin binding a credential they did not create gets a 404); the UI
 * only avoids offering a bind that cannot succeed, and avoids reading "missing
 * from my credential list" as "deleted" when the list is owner-scoped.
 */
import { ORG_ADMIN } from '@/shared/auth';
import type { Credential } from '@/shared/credentials/api';

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
 * misclassify every pick as "needs a new credential".
 */
export function credentialsBindableBy(
	credentials: Credential[],
	viewer: BindViewer | null | undefined,
): Credential[] {
	if (!viewer || viewerIsOrgAdmin(viewer)) return credentials;
	return credentials.filter((c) => c.created_by === viewer.id);
}
