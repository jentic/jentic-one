/**
 * LifecycleDialogs — the confirm-dialog orchestration for the destructive
 * lifecycle verbs (deny → reason required, disable → confirm, archive →
 * type-to-confirm cascade), shared by the Agents and Service-accounts tabs.
 *
 * Extracted from the two near-identical page sections so the retry contract
 * lives in one place: a failed mutation toasts (the hooks own that) and the
 * dialog stays open so the operator can retry; success closes it. The
 * stateless confirms are conditionally mounted (no drafts to lose — the
 * dialog-state-lifecycle rule's picker/confirm exception); `DenyDialog` owns
 * its reason draft internally.
 */
import { CascadeDeleteDialog } from '@/shared/ui';
import { DenyDialog } from '@/modules/agents/components/confirm/DenyDialog';
import { ConfirmDialog } from '@/modules/agents/components/confirm/ConfirmDialog';

/** A destructive lifecycle action awaiting confirmation in a dialog. */
export type PendingConfirm =
	| { kind: 'deny'; id: string; name: string }
	| { kind: 'disable'; id: string; name: string }
	| { kind: 'archive'; id: string; name: string }
	| null;

/** The slice of the TanStack mutation objects the dialogs need. */
interface ConfirmMutations {
	deny: {
		mutateAsync: (vars: { id: string; reason: string }) => Promise<unknown>;
		isPending: boolean;
	};
	disable: { mutateAsync: (id: string) => Promise<unknown>; isPending: boolean };
	archive: {
		mutateAsync: (id: string) => Promise<unknown>;
		isPending: boolean;
		error: Error | null;
	};
}

interface LifecycleDialogsProps {
	confirm: PendingConfirm;
	onClose: () => void;
	entityType: 'agent' | 'service-account';
	/** Consequence copy for the disable confirm (differs per actor kind). */
	disableBody: string;
	mutations: ConfirmMutations;
}

export function LifecycleDialogs({
	confirm,
	onClose,
	entityType,
	disableBody,
	mutations,
}: LifecycleDialogsProps) {
	const { deny, disable, archive } = mutations;

	return (
		<>
			<DenyDialog
				open={confirm?.kind === 'deny'}
				subjectName={confirm?.kind === 'deny' ? confirm.name : null}
				pending={deny.isPending}
				onConfirm={async (reason) => {
					if (confirm?.kind !== 'deny') return;
					try {
						await deny.mutateAsync({ id: confirm.id, reason });
						onClose();
					} catch {
						// onError toasts; keep the dialog open so the user can retry.
					}
				}}
				onClose={onClose}
			/>

			<ConfirmDialog
				open={confirm?.kind === 'disable'}
				title={confirm?.kind === 'disable' ? `Disable ${confirm.name}` : 'Disable'}
				body={disableBody}
				confirmLabel="Disable"
				pending={disable.isPending}
				onConfirm={async () => {
					if (confirm?.kind !== 'disable') return;
					try {
						await disable.mutateAsync(confirm.id);
						onClose();
					} catch {
						// onError toasts; keep the dialog open so the user can retry.
					}
				}}
				onClose={onClose}
			/>

			{confirm?.kind === 'archive' && (
				<CascadeDeleteDialog
					open
					entityType={entityType}
					entityName={confirm.name}
					loading={archive.isPending}
					error={archive.error}
					onConfirm={async () => {
						try {
							await archive.mutateAsync(confirm.id);
							onClose();
						} catch {
							// onError toasts; keep the dialog open so the user can retry.
						}
					}}
					onClose={onClose}
				/>
			)}
		</>
	);
}
