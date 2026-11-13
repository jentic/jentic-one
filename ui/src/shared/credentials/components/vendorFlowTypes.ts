import type { ReactNode } from 'react';
import type { VendorSummary } from '@/shared/credentials/api/vendors-types';

/**
 * Info handed to ``renderPostConnect`` on a successful connect. Callers
 * use it to build the "Bind to more agents" CTA (or anything else) —
 * that lives in ``modules/agents`` because layering forbids ``shared/``
 * from importing module code, so this component takes a render prop
 * instead of composing the CTA inline.
 */
export interface PostConnectInfo {
	credentialId: string;
	// The agent the credential was just bound to at ``:confirm``. Null
	// only for the (rare) case where the session had no target agent —
	// callers can decide whether to show anything in that state.
	boundAgentId: string | null;
}

export type VendorConnectFlowProps =
	| {
			mode: 'self';
			vendor: VendorSummary;
			// When set, the flow opens with the given agent pre-selected and
			// the picker rendered disabled. Used by the "Bind credential"
			// entry for a specific agent so the user can't
			// accidentally re-target during binding.
			preselectedAgentId?: string;
			// Extra content rendered on the terminal step's success path
			// (typically a "Bind to more agents" CTA). See ``PostConnectInfo``.
			renderPostConnect?: (info: PostConnectInfo) => ReactNode;
			/** Fires once when the sign-in completes, with the credential it created. */
			onConnected?: (info: ConnectedCredentialInfo) => void;
			onBack: () => void;
			onDone: () => void;
	  }
	| {
			mode: 'approve';
			sessionId: string;
			pollToken?: string;
			renderPostConnect?: (info: PostConnectInfo) => ReactNode;
			onBack: () => void;
			onDone: () => void;
	  }
	| {
			/**
			 * A person adding scopes to a binding's credential: their own change,
			 * applied at once through an edit session, via the vendor sign-in.
			 */
			mode: 'edit-scopes';
			agentId: string;
			credentialId: string;
			credentialName: string;
			vendorKey: string;
			onRegisterDismiss?: (handler: (() => void) | null) => void;
			onBack: () => void;
			onDone: () => void;
	  };

/** The credential a completed self connect created (and bound, when an agent was set). */
export interface ConnectedCredentialInfo {
	credentialId: string;
	name: string;
}

export interface VendorDisplay {
	displayName: string;
	iconKey: string;
	/**
	 * The admin-registered shared app this connection goes through, when the
	 * picked tile was one — so the configure step still says *which* app,
	 * not just the vendor it shares a name with.
	 */
	sharedAppName?: string;
}

export type Phase = 'configure' | 'rules' | 'awaiting' | 'terminal';
