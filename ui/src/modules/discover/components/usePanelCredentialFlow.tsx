/**
 * The Library's in-place "add a credential for this API" flow, opened from
 * the workspace panel's "no credential" attention item (docked card or
 * mobile sheet). Mounts the shared `CreateCredentialFlow` once, seeded on the
 * clicked API's form (the same `initialApiFor` seed the API hub uses), runs
 * the shared OAuth connect-after-create, and — once the credential is actually
 * usable — raises a transient "Credential added for …" notice for the panel.
 *
 * The flow's own "Credential created" toast is untouched; the notice is the
 * panel-local confirmation next to the list it just changed (the create
 * invalidates the credentials slice, so the API drops off "no credential"
 * on the refetch).
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import {
	CreateCredentialFlow,
	type CreatedCredentialInfo,
} from '@/shared/credentials/components/CreateCredentialFlow';
import { useConnectAfterCreate } from '@/shared/credentials/components/useConnectAfterCreate';
import { initialApiFor } from '@/shared/credentials/lib/initialApiFor';
import type { WorkspaceDigestRow } from '@/modules/discover/api';

/** How long the panel's "Credential added" notice stays up. */
const CREDENTIAL_NOTICE_MS = 7000;

export interface CredentialAddedNotice {
	/** Changes per success, so a repeat for the same API restarts the timer. */
	id: number;
	/** The API's display title. */
	label: string;
}

export interface PanelCredentialFlow {
	/** Open the flow on this API's form (step 2). */
	addCredentialFor: (row: WorkspaceDigestRow) => void;
	/** The latest success notice, until it times out or is dismissed. */
	notice: CredentialAddedNotice | null;
	dismissNotice: () => void;
	/** The flow + OAuth device dialog — render once in the page. */
	element: ReactNode;
}

export function usePanelCredentialFlow({
	noticeMs = CREDENTIAL_NOTICE_MS,
}: { noticeMs?: number } = {}): PanelCredentialFlow {
	const [open, setOpen] = useState(false);
	// Kept after close (dialog-state-lifecycle): the seed must not flip while
	// the drawer animates out; a different API re-seeds the flow on next open.
	const [target, setTarget] = useState<WorkspaceDigestRow | null>(null);
	const [notice, setNotice] = useState<CredentialAddedNotice | null>(null);
	const { afterCreate, deviceDialog } = useConnectAfterCreate();

	const initialApi = useMemo(
		() =>
			target
				? initialApiFor({
						ref: target.ref,
						catalogApiId: target.catalogApiId,
						securitySchemes: target.securitySchemes,
						label: target.title,
					})
				: undefined,
		[target],
	);

	const addCredentialFor = useCallback((row: WorkspaceDigestRow) => {
		setTarget(row);
		setOpen(true);
	}, []);
	const dismissNotice = useCallback(() => setNotice(null), []);

	useEffect(() => {
		if (!notice) return;
		const timer = setTimeout(() => setNotice(null), noticeMs);
		return () => clearTimeout(timer);
	}, [notice, noticeMs]);

	const label = target?.title ?? '';
	const element = (
		<>
			<CreateCredentialFlow
				open={open}
				onClose={() => setOpen(false)}
				initialApi={initialApi}
				onCreated={(info: CreatedCredentialInfo) => {
					setOpen(false);
					// Only a usable credential earns the notice: at once when no
					// sign-in is needed, else once the OAuth connect succeeds — a
					// discarded (cancelled / failed) sign-in raises none.
					afterCreate(info, () =>
						setNotice((prev) => ({ id: (prev?.id ?? 0) + 1, label })),
					);
				}}
			/>
			{deviceDialog}
		</>
	);

	return { addCredentialFor, notice, dismissNotice, element };
}
