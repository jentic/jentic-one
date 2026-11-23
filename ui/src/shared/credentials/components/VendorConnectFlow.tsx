import { EditScopesFlow } from './EditScopesFlow';
import { VendorApproveFlow } from './VendorApproveFlow';
import { VendorSelfConnectFlow } from './VendorSelfConnectFlow';
import type { VendorConnectFlowProps } from './vendorFlowTypes';

export type {
	ConnectedCredentialInfo,
	PostConnectInfo,
	VendorConnectFlowProps,
} from './vendorFlowTypes';

/**
 * Mode dispatcher for vendor connection and credential scope updates:
 *
 *  * `self` — the current user is opening the flow from the credential inventory,
 *    picks an agent + scopes, then runs start-and-confirm in one shot.
 *  * `approve` — an agent already started the session; the current user is the
 *    human owner (or an org admin) following the emitted approval URL. Session
 *    (with the agent's requested scopes) already exists; we skip the picker,
 *    load the review data, and go straight to confirm → device code → poll.
 *    `pollToken` is optional: the approval URL carries only the session id, and
 *    the backend authorises the agent's owner / an org admin without it. An
 *    older link that still carries a token keeps working for its holder.
 *    Beyond a vendor sign-in, approve mode resolves `manual_*` sessions (the
 *    approver types the API key / token / password), `awaiting_app` sessions
 *    (the approver's own OAuth client, or a shared app where the host offers
 *    one), binding a credential the approver already holds, and edit sessions.
 *    "Not now" closes with no server call; "Reject" is the explicit `:reject`.
 *  * `edit-scopes` — a person changing OAuth scopes on a binding's credential,
 *    applied through an edit session via the vendor sign-in.
 */
export function VendorConnectFlow(props: VendorConnectFlowProps) {
	if (props.mode === 'edit-scopes') {
		return (
			<EditScopesFlow
				agentId={props.agentId}
				credentialId={props.credentialId}
				credentialName={props.credentialName}
				vendorKey={props.vendorKey}
				onRegisterDismiss={props.onRegisterDismiss}
				onBack={props.onBack}
				onDone={props.onDone}
			/>
		);
	}
	if (props.mode === 'approve') {
		return (
			<VendorApproveFlow
				sessionId={props.sessionId}
				pollToken={props.pollToken}
				renderPostConnect={props.renderPostConnect}
				onBack={props.onBack}
				onDone={props.onDone}
			/>
		);
	}
	return (
		<VendorSelfConnectFlow
			vendor={props.vendor}
			preselectedAgentId={props.preselectedAgentId}
			renderPostConnect={props.renderPostConnect}
			onConnected={props.onConnected}
			onBack={props.onBack}
			onDone={props.onDone}
		/>
	);
}
