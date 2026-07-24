/**
 * AccessRequestDecisionDialog — routes an access request to the right decision UI.
 *
 * A *provisioning plan* (a request carrying `toolkit:create` / `credential:provision`
 * intents) must be decided through the fulfilment wizard, which creates the real
 * toolkit + credential and wires them before approving. Approving a plan through
 * the plain approve/deny dialog leaves the bind items unfulfilled and the backend
 * denies them (the setup is a no-op). Any surface that lets an operator decide a
 * request (dashboard queue, pending card, agent card) should open THIS wrapper
 * with the full request so the plan-vs-plain routing happens in one place.
 */
import { AccessRequestDialog } from '@/shared/app/rail/AccessRequestDialog';
import { ProvisioningRequestDialog } from '@/shared/app/rail/ProvisioningRequestDialog';
import { isProvisioningPlan, type AccessRequest } from '@/shared/lib';

export interface AccessRequestDecisionDialogProps {
	/** The request to decide; null closes the dialog. Full object so we can route. */
	request: AccessRequest | null;
	onClose: () => void;
	/** Called after a decision/fulfilment so the caller can refresh its list. */
	onDecided: () => void;
}

export function AccessRequestDecisionDialog({
	request,
	onClose,
	onDecided,
}: AccessRequestDecisionDialogProps) {
	if (request && isProvisioningPlan(request)) {
		return (
			<ProvisioningRequestDialog
				open
				request={request}
				onClose={onClose}
				onFulfilled={onDecided}
			/>
		);
	}
	return (
		<AccessRequestDialog
			open={request !== null}
			requestId={request?.id ?? null}
			onClose={onClose}
			onDecided={onDecided}
		/>
	);
}
