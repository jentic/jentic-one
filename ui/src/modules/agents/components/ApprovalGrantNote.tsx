/**
 * What approving a pending agent grants, in one line — so every Approve says
 * the same thing the first-run card does (`approvalGrant`): the default agent
 * permissions when it requested none, else the recognised permissions it requested.
 * The fleet banner, the "Waiting for approval" banner, the dock and the
 * Permissions sheet all read it, so no Approve button is silent about permissions.
 */
import { useAgentPermissions, usePermissionCatalogue } from '@/modules/agents/api';
import { cn } from '@/shared/lib/utils';
import { approvalGrant } from '@/modules/agents/lib/requestedPermissions';

/** "Approving grants the default agent permissions (11)." — `null` until both reads land
 * (or when `agentId` is null). */
export function useApprovalGrantCopy(agentId: string | null): string | null {
	const permissions = useAgentPermissions(agentId);
	const catalogue = usePermissionCatalogue({ enabled: agentId != null });
	if (agentId == null || !permissions.data || !catalogue.data) return null;
	const grant = approvalGrant(
		permissions.data,
		catalogue.data.map((p) => p.name),
	);
	const count = grant.granted.length;
	if (grant.kind === 'defaults')
		return `Approving grants the default agent permissions (${count}).`;
	if (count === 0) return 'Approving grants no permissions — none it requests is recognised.';
	return `Approving grants the ${count === 1 ? 'permission' : `${count} permissions`} it requests.`;
}

/** The copy as a quiet line (renders nothing until it's known). */
export function ApprovalGrantNote({
	agentId,
	id,
	className,
}: {
	agentId: string;
	/** For an Approve button's `aria-describedby`. */
	id?: string;
	className?: string;
}) {
	const copy = useApprovalGrantCopy(agentId);
	if (!copy) return null;
	return (
		<span id={id} data-testid="approval-grant-note" className={cn('text-xs', className)}>
			{copy}
		</span>
	);
}
