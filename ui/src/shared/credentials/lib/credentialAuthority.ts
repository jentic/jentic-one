/**
 * Who may change a credential, as the UI mirrors it. The server enforces the
 * rule (a non-admin editing or deleting a credential they did not create gets a
 * 404); the UI only avoids offering an edit that cannot succeed. A credential
 * can still be listed for a caller who did not create it — it is shared with
 * them — and those read as "Shared with you", without Edit or Delete.
 */
import { ORG_ADMIN } from '@/shared/auth';
import type { Credential } from '@/shared/credentials/api';

/** The slice of the signed-in user these rules read. `null` = not known yet
 * (still loading, or rendered outside an `AuthProvider`). */
export interface CredentialViewer {
	id: string;
	permissions?: readonly string[] | null;
}

/**
 * An `org:admin` edits any credential; anyone else only the ones they created.
 * An unknown viewer is treated as able to edit — the server still enforces, and
 * hiding every action while `/users/me` loads would flash the whole list
 * read-only.
 */
export function credentialEditableBy(
	cred: Pick<Credential, 'created_by'>,
	viewer: CredentialViewer | null | undefined,
): boolean {
	if (!viewer) return true;
	if (viewer.permissions?.includes(ORG_ADMIN)) return true;
	return cred.created_by != null && cred.created_by === viewer.id;
}
