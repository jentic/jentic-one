/**
 * Pure helpers for the connect-session approve dialog: which flow a session
 * resolved to, how its declared scheme reads, why a viewer cannot confirm it,
 * and what a failed `:confirm` means for the dialog.
 */
import type {
	ExistingCredential,
	ReviewScheme,
	ReviewSession,
} from '@/shared/credentials/api/vendors-types';

/** The secret a `manual_*` session collects, keyed by its `resolved_flow`. */
export type SecretKind = 'api_key' | 'bearer' | 'basic';

const MANUAL_FLOWS: Readonly<Record<string, SecretKind>> = {
	manual_api_key: 'api_key',
	manual_bearer: 'bearer',
	manual_basic: 'basic',
};

/** The secret kind a session collects, or null when it is not a `manual_*` flow. */
export function secretKindOf(resolvedFlow: string): SecretKind | null {
	return MANUAL_FLOWS[resolvedFlow] ?? null;
}

/** An OAuth API target with no app to connect through yet. */
export function isAwaitingApp(session: Pick<ReviewSession, 'state'>): boolean {
	return session.state === 'awaiting_app';
}

/** Noun for the secret a scheme asks for ("API key", "bearer token", …). */
export function secretNoun(kind: SecretKind): string {
	if (kind === 'api_key') return 'API key';
	if (kind === 'bearer') return 'bearer token';
	return 'username and password';
}

/** "API key in the `X-Api-Key` header" — where the declared scheme sends the secret. */
export function schemeSummary(scheme: ReviewScheme): string {
	if (scheme.type === 'bearer') return 'Bearer token in the Authorization header';
	if (scheme.type === 'basic') return 'Username and password (HTTP Basic)';
	if (scheme.type === 'oauth2') return 'OAuth 2.0';
	const where = scheme.location ? ` ${scheme.location}` : '';
	return scheme.field_name
		? `API key in the ${scheme.field_name}${where}`
		: `API key${where ? ` in the${where}` : ''}`;
}

/** Whether the API's live revision came from the public catalog. */
export function isCatalogProvenance(session: Pick<ReviewSession, 'provenance'>): boolean {
	return session.provenance?.origin === 'catalog';
}

/**
 * Why this viewer cannot confirm the session, in plain words — shown in
 * place of secret entry so nobody types a key that would be refused.
 */
export function confirmBlockedReason(session: ReviewSession): string {
	if (session.state === 'polling') {
		return 'This request is already waiting on a vendor sign-in, so there is nothing left to approve here.';
	}
	if (session.state === 'connected') {
		return 'This request is already connected.';
	}
	const status = session.agent?.status;
	if (status === 'archived' || status === 'disabled' || status === 'rejected') {
		return `This agent is ${status}, so it can't be given a credential.`;
	}
	if (session.agent == null) {
		return "The agent this request is for no longer exists, so it can't be given a credential.";
	}
	return (
		"Only the agent's owner (allowed to manage both credentials and agents) or an org " +
		"admin can approve this request. You can see it, but you can't approve or reject it."
	);
}

/** What the dialog does after a failed `:confirm`. */
export type ConfirmErrorAction =
	/** The session is still open but what was reviewed changed: reload and review again. */
	| 'reload'
	/** Back to the rules page. */
	| 'rules'
	/** The session is over (or can never be confirmed by this viewer). */
	| 'ended'
	/** Stay on the current step and let the approver retry. */
	| 'stay';

export interface ConfirmErrorCopy {
	message: string;
	action: ConfirmErrorAction;
}

interface ProblemLike {
	status?: number | null;
	code?: string | null;
	message?: string;
	extensions?: Readonly<Record<string, unknown>>;
}

function stringList(value: unknown): string[] {
	return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : [];
}

/**
 * Map a failed `:confirm` to approver-facing copy and the dialog's next step.
 * Branches on the problem `type` slug; never echoes what the approver typed
 * (the backend never returns it, and the copy here is static).
 */
export function describeConfirmError(error: unknown): ConfirmErrorCopy {
	const problem = (error ?? {}) as ProblemLike;
	switch (problem.code) {
		case 'review_stale':
		case 'confirm_kind_mismatch':
			return {
				action: 'reload',
				message:
					'This request changed while you were reviewing it. Check the details again before you approve.',
			};
		case 'scheme_changed':
			return {
				action: 'ended',
				message:
					"The API's declared authentication changed after the agent asked, so this request was closed. The agent needs to ask again.",
			};
		case 'servers_changed':
			return {
				action: 'ended',
				message:
					"The API's server hosts changed after the agent asked, so this request was closed. The agent needs to ask again.",
			};
		case 'rules_required':
			return {
				action: 'rules',
				message:
					"Add at least one rule. With no rules the agent can't call anything on this API.",
			};
		case 'connect_session_agent_inactive':
		case 'connect_session_agent_not_found':
			return {
				action: 'ended',
				message:
					"The agent is archived, disabled or gone, so it can't be given this credential.",
			};
		case 'connect_session_invalid_state':
			return {
				action: 'ended',
				message: 'This request is no longer open: it was completed, cancelled or expired.',
			};
		case 'connect_session_oauth_app_changed':
			return {
				action: 'ended',
				message:
					'The OAuth app this request connects through was changed or removed. The agent needs to ask again.',
			};
		case 'insufficient_granted_scopes': {
			const missing = stringList(problem.extensions?.missing_scopes);
			return {
				action: 'reload',
				message: `That credential's sign-in doesn't cover everything the agent asked for${
					missing.length ? ` (missing ${missing.join(', ')})` : ''
				}. Pick another option.`,
			};
		}
		case 'reauthorize_unavailable':
			return {
				action: 'reload',
				message:
					"That credential is used by other agents, so it can't be re-authorized with more scopes. Connect a new credential instead.",
			};
		case 'existing_credential_not_found':
			return {
				action: 'reload',
				message: 'That credential is no longer available to you. Pick another option.',
			};
		case 'own_oauth_client_invalid':
			return {
				action: 'stay',
				message:
					"Check the authorize and token URLs. This API doesn't declare them, or one isn't a valid public HTTPS URL.",
			};
		case 'connect_session_confirmation_forbidden':
		case 'invalid_poll_token':
			return {
				action: 'ended',
				message: "This request is no longer open, or it isn't yours to approve.",
			};
		default:
			break;
	}
	if (problem.status === 403) {
		return {
			action: 'ended',
			message: "This request is no longer open, or it isn't yours to approve.",
		};
	}
	if (problem.status === 429) {
		return { action: 'stay', message: 'Too many attempts. Wait a moment and try again.' };
	}
	return {
		action: 'stay',
		message: problem.message || 'Something went wrong. Try again.',
	};
}

/** "Used by 2 other agents" — the reason a credential can't be widened. */
export function sharedWithCount(credential: ExistingCredential): string {
	const n = credential.other_bound_agent_ids.length;
	return n === 1 ? 'Used by 1 other agent' : `Used by ${n} other agents`;
}
