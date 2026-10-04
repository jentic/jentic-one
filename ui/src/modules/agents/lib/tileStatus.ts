/**
 * The ONE status an API tile (and its access sheet) claims, derived in a fixed
 * precedence so two lines on the same card can never contradict each other
 * (a green "Ready" beside "No rules — all calls blocked" was exactly that).
 *
 *   Suspended → Not serving → Sign-in needed → Blocked → Ready
 *
 * A binding is default-deny: with no operator rules every call is refused, and
 * with only deny rules nothing is allowed either — both read as Blocked. Rules
 * still loading (or unreadable) prove nothing, so the tile keeps "Ready" rather
 * than guess a block it can't show; the summary line is omitted the same way.
 */
import type { BindingRuleSummary } from '@/modules/agents/api';

export type TileStatus =
	| 'suspended'
	| 'not-serving'
	| 'sign-in-needed'
	| 'blocked-no-rules'
	| 'blocked-all-denied'
	| 'ready';

export interface TileStatusInput {
	/** The binding is paused. */
	suspended: boolean;
	/** The AGENT serves traffic (only `active` agents do). */
	agentServing: boolean;
	/** The credential's vendor sign-in hasn't completed. */
	awaitingConsent: boolean;
	/** Operator-rule breakdown; `undefined` while unknown. */
	rules: BindingRuleSummary | undefined;
}

export function deriveTileStatus({
	suspended,
	agentServing,
	awaitingConsent,
	rules,
}: TileStatusInput): TileStatus {
	if (suspended) return 'suspended';
	if (!agentServing) return 'not-serving';
	if (awaitingConsent) return 'sign-in-needed';
	return rulesBlock(rules) ?? 'ready';
}

type BlockedStatus = 'blocked-no-rules' | 'blocked-all-denied';

/** Which Blocked flavour these rules amount to — null when some call gets
 * through, or while the rules are unknown. */
export function rulesBlock(rules: BindingRuleSummary | undefined): BlockedStatus | null {
	if (!rules) return null;
	if (rules.total === 0) return 'blocked-no-rules';
	if (rules.allow === 0) return 'blocked-all-denied';
	return null;
}

/** Both Blocked flavours — the status the rules editor fixes. */
export function isBlockedStatus(status: TileStatus): status is BlockedStatus {
	return status === 'blocked-no-rules' || status === 'blocked-all-denied';
}

/** The word(s) each status prints. */
export const TILE_STATUS_LABEL: Record<TileStatus, string> = {
	suspended: 'Suspended · not serving',
	'not-serving': 'Not serving',
	'sign-in-needed': 'Sign-in needed',
	'blocked-no-rules': 'Blocked · no rules',
	'blocked-all-denied': 'Blocked · all denied',
	ready: 'Ready',
};

/** The shorter form for a chip beside a title (the sheet header). */
export const TILE_STATUS_CHIP_LABEL: Record<TileStatus, string> = {
	...TILE_STATUS_LABEL,
	suspended: 'Suspended',
};

/** Tooltip / accessible hint for the Blocked statuses' button. */
export const BLOCKED_HINT: Record<BlockedStatus, string> = {
	'blocked-no-rules': 'No access rules yet — every call is denied. Add a rule.',
	'blocked-all-denied': 'Every rule denies — no call is allowed. Add an allow rule.',
};
