/**
 * CredentialDeleteDialog — wraps the shared `CascadeDeleteDialog` with the
 * bound-agent read, so a delete confirm names which agents lose access.
 *
 * `dependents` are passed only once every page has landed: while loading or on
 * failure the dialog falls back to its generic warning rather than implying
 * nothing else is affected.
 */
import { Button, CascadeDeleteDialog, Dialog, type CascadeDependentGroup } from '@/shared/ui';
import { useOptionalCurrentUser } from '@/shared/auth';
import { useAllCredentialAgents } from '@/shared/credentials/api';
import { viewerIsOrgAdmin } from '@/shared/credentials/lib/bindAuthority';
import { ManagedCredentialNote, type ManagedBy } from './managedBy';

/** How many agent names the blast radius lists before summarising the rest. The
 * headline count stays exact; this caps only the chip list. */
const NAME_LIMIT = 12;

export interface CredentialDeleteDialogProps {
	open: boolean;
	/** The credential about to be deleted org-wide. */
	credentialId: string;
	credentialName: string;
	onClose: () => void;
	onConfirm: () => void;
	loading?: boolean;
	error?: Error | string | null;
	/** Set when the host is one agent's own surface, so that agent is marked. */
	currentAgentId?: string;
	/**
	 * The credential's `managed_by`, when an extension manages it. Only an
	 * `org:admin` may delete such a credential here (the server answers 409
	 * `credential_managed` to anyone else), so everyone else gets a note
	 * saying where it is managed instead of the confirm.
	 */
	managedBy?: ManagedBy | null;
}

export function CredentialDeleteDialog({
	open,
	credentialId,
	credentialName,
	onClose,
	onConfirm,
	loading,
	error,
	currentAgentId,
	managedBy,
}: CredentialDeleteDialogProps) {
	const viewer = useOptionalCurrentUser();
	const blocked = managedBy != null && !viewerIsOrgAdmin(viewer);
	// Gated on `open` and keyed by credential. Every page is drained: the group
	// below states a COUNT, and a delete this irreversible must not understate it.
	const credentialAgents = useAllCredentialAgents(credentialId, { enabled: open && !blocked });
	const rows = credentialAgents.items;

	// An empty list is a real answer, but a "0 agents" headline reads worse than
	// the generic warning — and a partial drain would understate it.
	//
	// Deleting a credential does not remove its agent bindings (they stay behind,
	// pointing at nothing), so the copy says the agents lose access — never that
	// the delete removes them.
	const dependents: CascadeDependentGroup[] | undefined =
		credentialAgents.complete && rows.length > 0
			? [
					{
						label: 'bound agent',
						count: rows.length,
						names: [
							...rows
								.slice(0, NAME_LIMIT)
								.map((row) =>
									row.agent_id === currentAgentId
										? `${row.agent_name} (this agent)`
										: row.agent_name,
								),
							...(rows.length > NAME_LIMIT
								? [`…and ${rows.length - NAME_LIMIT} more`]
								: []),
						],
					},
				]
			: undefined;

	if (blocked && managedBy) {
		return (
			<Dialog
				open={open}
				onClose={onClose}
				title="This credential can't be deleted here"
				subtitle={credentialName}
				footer={
					<Button variant="ghost" onClick={onClose}>
						Close
					</Button>
				}
			>
				<ManagedCredentialNote managedBy={managedBy} />
			</Dialog>
		);
	}

	return (
		<CascadeDeleteDialog
			open={open}
			onClose={onClose}
			onConfirm={onConfirm}
			entityType="credential"
			entityName={credentialName}
			dependents={dependents}
			dependentsHeadline={
				rows.length === 1
					? '1 agent uses this credential and will lose access to it.'
					: `${rows.length} agents use this credential and will lose access to it.`
			}
			loading={loading}
			error={error}
		/>
	);
}
