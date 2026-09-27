/**
 * Audit actions in plain language.
 *
 * The backend records an action as `<noun>.<verb>` (`job.cancel`,
 * `credential.rotate`, `user.login_failed`). The log shows a sentence instead
 * — "Cancelled a job", "Rotated a credential", "Failed to sign in" — and keeps
 * the raw action in the detail pane for anyone who needs to grep for it.
 *
 * Unknown verbs and nouns degrade to a readable humanisation ("Frobbed a
 * widget thing"), never to a blank, so a new backend action shows up
 * sensibly before this dictionary learns it.
 */
import type { AuditResponse } from '@/modules/monitor/api';

/** Past tense for the backend's audit verbs. */
const VERB: Record<string, string> = {
	create: 'Created',
	update: 'Updated',
	delete: 'Deleted',
	promote: 'Promoted',
	demote: 'Demoted',
	enable: 'Enabled',
	disable: 'Disabled',
	revoke: 'Revoked',
	grant: 'Granted',
	approve: 'Approved',
	deny: 'Denied',
	archive: 'Archived',
	rotate: 'Rotated',
	refresh: 'Refreshed',
	confirm: 'Confirmed',
	deprecate: 'Deprecated',
	register: 'Registered',
	claim: 'Claimed',
	start: 'Started',
	cancel: 'Cancelled',
};

/** Verbs that already say everything — the noun would only repeat itself. */
const WHOLE: Record<string, string> = {
	login: 'Signed in',
	login_failed: 'Failed to sign in',
	logout: 'Signed out',
	api_key_rotated: 'Rotated an API key',
	api_key_revoked: 'Revoked an API key',
	client_secret_rotated: 'Rotated a client secret',
};

/** The backend's audit target types, with their article. */
const NOUN: Record<string, string> = {
	user: 'a user',
	credential: 'a credential',
	toolkit: 'a toolkit',
	agent: 'an agent',
	job: 'a job',
	organisation: 'the organisation',
	invite_token: 'an invite',
	event: 'an event',
	execution: 'an execution',
	execution_record: 'an execution',
	service_account: 'a service account',
	token: 'a token',
	overlay: 'an overlay',
	note: 'a note',
	api: 'an API',
	access_request: 'an access request',
	toolkit_key: 'a toolkit key',
	credential_binding: 'a credential binding',
	permission_rule_set: 'a permission rule set',
	session: 'a session',
	provider_config: 'a provider config',
	oauth_client: 'an OAuth client',
	oauth_grant: 'an OAuth grant',
};

/** Actions worth a second look — shown with a warning glyph. */
const WARN_VERBS = new Set(['delete', 'revoke', 'disable', 'deny', 'cancel', 'api_key_revoked']);
const FAIL_VERBS = new Set(['login_failed']);

export type AuditTone = 'fail' | 'warn' | 'neutral';

function humanise(token: string): string {
	return token.replace(/[_-]+/g, ' ').trim();
}

function capitalise(text: string): string {
	return text ? text[0].toUpperCase() + text.slice(1) : text;
}

function nounPhrase(noun: string): string {
	return NOUN[noun] ?? `a ${humanise(noun)}`;
}

function splitAction(action: string): { noun: string; verb: string } {
	const dot = action.lastIndexOf('.');
	return dot < 0
		? { noun: '', verb: action }
		: { noun: action.slice(0, dot), verb: action.slice(dot + 1) };
}

/** "Cancelled a job" — the log row's sentence for one audit entry. */
export function auditSentence(row: Pick<AuditResponse, 'action' | 'target_type'>): string {
	const { noun: actionNoun, verb } = splitAction(row.action);
	if (WHOLE[verb]) return WHOLE[verb];
	const noun = nounPhrase(actionNoun || row.target_type);
	const past = VERB[verb] ?? capitalise(`${humanise(verb)}`);
	return `${past} ${noun}`;
}

export function auditTone(row: Pick<AuditResponse, 'action'>): AuditTone {
	const { verb } = splitAction(row.action);
	if (FAIL_VERBS.has(verb)) return 'fail';
	if (WARN_VERBS.has(verb)) return 'warn';
	return 'neutral';
}

/** The target's type as a short label for the Target column ("Job", "Execution"). */
export function auditTargetLabel(targetType: string): string {
	const phrase = NOUN[targetType];
	const bare = phrase ? phrase.replace(/^(a|an|the) /, '') : humanise(targetType);
	return capitalise(bare);
}

const ISO_DATETIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;

function formatValue(value: unknown): string {
	if (value == null) return '—';
	if (typeof value === 'string') {
		// Timestamps read as dates, not wire strings.
		if (ISO_DATETIME.test(value)) {
			return new Date(value).toLocaleString(undefined, {
				month: 'short',
				day: 'numeric',
				year: 'numeric',
				hour: '2-digit',
				minute: '2-digit',
			});
		}
		return value;
	}
	if (typeof value === 'number' || typeof value === 'boolean') return String(value);
	return JSON.stringify(value);
}

export interface AuditChange {
	field: string;
	before: string;
	after: string;
}

/**
 * The fields an entry changed, before → after. Prefers the recorded `diff`
 * (`{field: [before, after]}` or `{field: {before, after}}`), else compares
 * `before` and `after` key by key.
 */
export function auditChanges(row: Pick<AuditResponse, 'before' | 'after' | 'diff'>): AuditChange[] {
	if (row.diff && Object.keys(row.diff).length > 0) {
		return Object.entries(row.diff).map(([field, value]) => {
			if (Array.isArray(value) && value.length === 2)
				return { field, before: formatValue(value[0]), after: formatValue(value[1]) };
			if (value && typeof value === 'object' && ('before' in value || 'after' in value)) {
				const v = value as { before?: unknown; after?: unknown };
				return { field, before: formatValue(v.before), after: formatValue(v.after) };
			}
			return { field, before: '—', after: formatValue(value) };
		});
	}
	const before = row.before ?? {};
	const after = row.after ?? {};
	const fields = [...new Set([...Object.keys(before), ...Object.keys(after)])];
	return fields
		.filter((f) => JSON.stringify(before[f]) !== JSON.stringify(after[f]))
		.map((field) => ({
			field,
			before: formatValue(before[field]),
			after: formatValue(after[field]),
		}));
}

/** One-line summary of the changes for the row's second line ("status running → cancelled"). */
export function auditChangeSummary(changes: AuditChange[]): string | null {
	if (changes.length === 0) return null;
	const [first, ...rest] = changes;
	const head = `${humanise(first.field)} ${first.before} → ${first.after}`;
	return rest.length ? `${head} · +${rest.length} more` : head;
}
