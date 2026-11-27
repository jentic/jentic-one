/**
 * The ONE status an API tile (and its access sheet) claims, derived in a fixed
 * precedence so two lines on the same card can never contradict each other
 * (a green "Ready" beside "No rules — all calls blocked", say).
 *
 *   Suspended → Expired → Not serving → Sign-in needed → Checking / Unavailable → Blocked
 *     → Rules out of date → Rules ambiguous → Ready
 *
 * Suspended comes before Expired, as the broker reports them: renewing alone
 * would not restore a suspended binding, so the pause is the thing to fix.
 *
 * A binding is default-deny: with no operator rules every call is refused, and
 * with only deny rules nothing is allowed either — both read as Blocked. Ready
 * is claimed only once the rules are READ: while they load the tile says
 * "Checking access…", and when the read fails it says "Status unavailable" — an
 * unknown binding may well be blocked, so it never looks healthy. Nor is Ready
 * claimed while an action rule names ids the API no longer has: that rule is
 * not in force, so the binding reads "Rules out of date".
 * An action rule the broker would misread — a shared `operationId` on an Ask or
 * Allow, or an action named in only some of the APIs that declare it — reads
 * "Rules ambiguous" for the same reason.
 */
import type { BindingRuleSummary, BindingRulesState } from '@/modules/agents/api';

export type TileStatus =
	| 'suspended'
	| 'expired'
	| 'not-serving'
	| 'sign-in-needed'
	| 'blocked-no-rules'
	| 'blocked-all-denied'
	| 'checking'
	| 'unavailable'
	| 'rules-outdated'
	| 'rules-ambiguous'
	| 'ready';

export interface TileStatusInput {
	/** The binding is paused. */
	suspended: boolean;
	/** The binding's end date has passed. */
	expired?: boolean;
	/** The AGENT serves traffic (only `active` agents do). */
	agentServing: boolean;
	/** The credential's vendor sign-in hasn't completed. */
	awaitingConsent: boolean;
	/** Operator-rule breakdown, or where its read stands (`undefined` = not read yet). */
	rules: BindingRulesState | undefined;
	/**
	 * An action rule names operation ids the API's live version no longer has.
	 * Ids change when the API is re-imported, so such a rule matches nothing:
	 * a deny or ask it carried no longer stops the call, which falls through to
	 * the rules after it. Known only where the API's operations are read (the
	 * access sheet); a tile that cannot tell leaves it unset.
	 */
	staleActionRules?: boolean;
	/**
	 * An action rule names an action whose `operationId` another covered API
	 * declares too, in a way the broker can't settle: an Ask or Allow by the
	 * shared name (refused, or granting nothing), or a rule naming the action
	 * in only some of the APIs (a call resolved to another skips it).
	 */
	ambiguousActionRules?: boolean;
}

export function deriveTileStatus({
	suspended,
	expired = false,
	agentServing,
	awaitingConsent,
	rules,
	staleActionRules = false,
	ambiguousActionRules = false,
}: TileStatusInput): TileStatus {
	if (suspended) return 'suspended';
	if (expired) return 'expired';
	if (!agentServing) return 'not-serving';
	if (awaitingConsent) return 'sign-in-needed';
	if (rules === 'error') return 'unavailable';
	if (rules === undefined || rules === 'loading') return 'checking';
	// Blocked outranks out-of-date: with nothing allowed no call gets through
	// whatever the stale rule says. Past that, a stale rule means the rules on
	// screen are not the rules in force, so the binding is never Ready.
	return (
		rulesBlock(rules) ??
		(staleActionRules ? 'rules-outdated' : ambiguousActionRules ? 'rules-ambiguous' : 'ready')
	);
}

/** The breakdown itself, or `undefined` while it is loading or unreadable. */
export function ruleSummaryOf(
	rules: BindingRulesState | undefined,
): BindingRuleSummary | undefined {
	return typeof rules === 'object' ? rules : undefined;
}

type BlockedStatus = 'blocked-no-rules' | 'blocked-all-denied';

/** Which Blocked flavour these rules amount to — null when some call gets
 * through, or while the rules are unknown. */
export function rulesBlock(rules: BindingRuleSummary | undefined): BlockedStatus | null {
	if (!rules) return null;
	if (rules.total === 0) return 'blocked-no-rules';
	// An Ask rule still lets its calls through, once a reviewer approves.
	if (rules.allow + rules.ask === 0) return 'blocked-all-denied';
	return null;
}

/** Both Blocked flavours — the status the rules editor fixes. */
export function isBlockedStatus(status: TileStatus): status is BlockedStatus {
	return status === 'blocked-no-rules' || status === 'blocked-all-denied';
}

/** The word(s) each status prints. */
export const TILE_STATUS_LABEL: Record<TileStatus, string> = {
	suspended: 'Suspended · not serving',
	expired: 'Expired · renew to restore',
	'not-serving': 'Not serving',
	'sign-in-needed': 'Sign-in needed',
	'blocked-no-rules': 'Blocked · no rules',
	'blocked-all-denied': 'Blocked · all denied',
	checking: 'Checking access…',
	unavailable: 'Status unavailable',
	'rules-outdated': 'Rules out of date',
	'rules-ambiguous': 'Rules ambiguous',
	ready: 'Ready',
};

/** The shorter form for a chip beside a title (the sheet header). */
export const TILE_STATUS_CHIP_LABEL: Record<TileStatus, string> = {
	...TILE_STATUS_LABEL,
	suspended: 'Suspended',
	expired: 'Expired',
};

/** The one-word form for a dense card's status line, where the long "· why"
 * tail rides on the card's tooltip instead. */
export const TILE_STATUS_CARD_LABEL: Record<TileStatus, string> = {
	suspended: 'Paused',
	expired: 'Expired',
	'not-serving': 'Not serving',
	'sign-in-needed': 'Sign-in needed',
	'blocked-no-rules': 'Blocked',
	'blocked-all-denied': 'Blocked',
	checking: 'Checking',
	unavailable: 'Unavailable',
	'rules-outdated': 'Out of date',
	'rules-ambiguous': 'Ambiguous',
	ready: 'Ready',
};

/** Tooltip / accessible hint for the Blocked statuses' button. */
export const BLOCKED_HINT: Record<BlockedStatus, string> = {
	'blocked-no-rules': 'No access rules yet — every call is denied. Add a rule.',
	'blocked-all-denied': 'Every rule denies — no call is allowed. Add an allow rule.',
};

/**
 * The binding's rules in one line — "3 access rules · 1 ask · 1 deny", led by the
 * shared rule set when one governs it ("Rule set Support · …") — or null while
 * the rules are unknown. The row prints it under its marker; the card's status
 * tooltip carries it.
 */
export function rulesSummaryText(rules: BindingRulesState | undefined): string | null {
	const summary = ruleSummaryOf(rules);
	if (summary === undefined) return null;
	const prefix = summary.ruleSet ? `Rule set ${summary.ruleSet.name} · ` : '';
	if (summary.total === 0) return `${prefix}No rules — all calls blocked`;
	const count = summary.total === 1 ? '1 access rule' : `${summary.total} access rules`;
	const splits = [
		summary.ask > 0 ? `${summary.ask} ask` : null,
		summary.deny > 0 ? `${summary.deny} deny` : null,
	].filter(Boolean);
	return `${prefix}${[count, ...splits].join(' · ')}`;
}
