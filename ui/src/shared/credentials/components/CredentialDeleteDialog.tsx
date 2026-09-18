/**
 * CredentialDeleteDialog — the credential delete confirm, with the agents that
 * lose access named.
 *
 * Deleting a credential is org-wide: every agent bound to it stops being able
 * to call the API. An operator can only weigh that if the confirm says WHICH
 * agents, not merely how many — so this wraps the shared `CascadeDeleteDialog`
 * with the bound-agent read, and each host passes the credential instead of
 * assembling the blast radius itself.
 *
 * Honesty contract: the dependent list is handed over only once the read has
 * SUCCEEDED. While it loads — or if it fails, or the caller isn't allowed the
 * read — no `dependents` are passed, so the dialog falls back to its generic
 * credential warning rather than implying that nothing else is affected.
 * The type-to-confirm gate is the shared dialog's and is unchanged.
 */
import { CascadeDeleteDialog, type CascadeDependentGroup } from '@/shared/ui';
import { useCredentialAgents } from '@/shared/credentials/api';

/** How many agent names the blast radius lists before it summarises the rest.
 * The count in the headline is always exact; this caps only the chip list, so a
 * credential shared by a whole fleet doesn't push the confirm button off-screen. */
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
	/** Set when the host is one agent's own surface, so that agent is marked in
	 * the list — "this agent" reads very differently from a stranger's name. */
	currentAgentId?: string;
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
}: CredentialDeleteDialogProps) {
	// Gated on `open`, and keyed by credential — a host that already made this
	// read (the API access sidebar shows the same agents) shares the cache
	// rather than issuing a second request.
	const credentialAgents = useCredentialAgents(credentialId, { enabled: open });
	const rows = credentialAgents.data?.data ?? [];

	// An empty list is a real answer, but "will also remove 0 dependents" is a
	// worse thing to read than the generic warning about what deleting a
	// credential costs — so nothing is passed and the generic copy stands.
	const dependents: CascadeDependentGroup[] | undefined =
		credentialAgents.isSuccess && rows.length > 0
			? [
					{
						label: 'agent binding',
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

	return (
		<CascadeDeleteDialog
			open={open}
			onClose={onClose}
			onConfirm={onConfirm}
			entityType="credential"
			entityName={credentialName}
			dependents={dependents}
			loading={loading}
			error={error}
		/>
	);
}
