/**
 * Manage access → Access, read view: what the agent can call through this API
 * under the SAVED rules — the heading, the summary and bar (shared with the
 * edit view, which shows the draft's), the attached shared rule set when
 * there is one, and the effective access of the binding, grouped by path.
 * Viewing never changes anything; the one verb is "Edit access".
 */
import type { ReactNode } from 'react';
import { AlertTriangle, Check, Lock, ShieldCheck, SlidersHorizontal } from 'lucide-react';
import { Button, TruncateWithTooltip } from '@/shared/ui';
import type { PermissionRule } from '@/shared/credentials/api/vendors-types';
import {
	OperationImpactPreview,
	useAllowedOperationCount,
	type OpsApiReference,
} from '@/shared/credentials/components';
import { presetForRules, splitCustomRules } from '@/shared/credentials/lib/accessPresets';
import {
	actionCount,
	actionOwner,
	describeActions,
	type ActionCatalog,
} from '@/shared/credentials/lib/actionRules';
import { plural } from '@/modules/agents/lib/stripSummary';
import { ownRules } from '@/modules/agents/lib/accessDraft';
import { AccessCounts } from '@/modules/agents/components/flat/access/AccessCounts';

/** How long the broker may keep serving a binding's previous rules after a
 * save: its rule cache's TTL (`broker.rule_cache_ttl_seconds`, 30 by default).
 * "Saved" must not read as "in force this instant". */
const RULE_CACHE_SECONDS = 30;

const PRESET_SENTENCE = {
	read: 'Read only — GET requests run, everything else is denied.',
	'read-ask':
		'Read only, ask before anything else — GET requests run; every other call asks first.',
	all: 'Full access — any method, any path.',
} as const;

/**
 * The rules in one line, naming the preset in use — for Custom rules, how
 * many actions are set, how many are out of date (ids the API's live version
 * no longer has, so they match nothing) and how many path rules follow, never
 * per-effect numbers (the bar and By action's header carry those). Only this
 * API's actions count (`actionOwner`), exactly as By action counts them.
 */
export function savedRulesSentence(
	rules: readonly PermissionRule[],
	catalog: ActionCatalog,
): string {
	// Another API's action rules on a shared credential are not this API's
	// rules: a preset saved from that API's panel is still the preset here.
	const own = ownRules(rules, catalog);
	const preset = presetForRules(own.length > 0 ? own : rules);
	if (preset === null) return 'No rules — every call is denied.';
	if (preset !== 'custom') return PRESET_SENTENCE[preset];
	const { pathRules, groups } = splitCustomRules(rules);
	const described = describeActions([...groups.allow, ...groups.ask, ...groups.deny], catalog);
	const set = described.entries.filter((e) => actionOwner(e, catalog) === 'own').length;
	const parts = [
		set > 0 && `${actionCount(set)} set`,
		described.missing > 0 && `${actionCount(described.missing)} out of date`,
		pathRules.length > 0 && plural(pathRules.length, 'path rule'),
	].filter(Boolean);
	return parts.length > 0
		? `Custom rules — ${parts.join(', ')}.`
		: 'Custom rules — none name this API, so every call to it is denied.';
}

export interface AccessSummaryProps {
	rules: readonly PermissionRule[];
	/** Names the rules' actions, so only this API's are counted. */
	catalog: ActionCatalog;
	apiReference: OpsApiReference | null;
	/** Whose rules these are: the saved ones, or the draft being edited. */
	source: 'saved' | 'draft';
	/** Before the sentence (e.g. the attached rule set's name). */
	prefix?: string;
	/** Beside the summary (Edit access). */
	action?: ReactNode;
}

/** The summary line and the allowed · ask · denied bar, for saved rules or the draft. */
export function AccessSummary({
	rules,
	catalog,
	apiReference,
	source,
	prefix,
	action,
}: AccessSummaryProps) {
	const counts = useAllowedOperationCount(apiReference, rules);
	return (
		<div className="flex flex-wrap items-start gap-3">
			<div className="min-w-0 flex-1 space-y-1.5">
				<p className="text-foreground text-sm" data-testid="access-summary">
					{prefix}
					{savedRulesSentence(rules, catalog)}
				</p>
				{counts && (
					<p className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
						<AccessCounts counts={counts} data-testid="access-counts" />
						<span
							className="text-foreground-faint text-xs"
							data-testid="access-counts-source"
						>
							{source === 'draft' ? 'your draft' : 'saved'}
						</span>
					</p>
				)}
			</div>
			{action}
		</div>
	);
}

/** "Permission rules for <credential>" — the rules belong to the agent's
 * binding of this credential, not to the agent or the API — with an optional
 * action at its end. */
export function AccessHeading({
	credentialName,
	after,
}: {
	credentialName: string;
	after?: ReactNode;
}) {
	return (
		<div className="flex flex-wrap items-center gap-x-1.5 gap-y-1">
			<h3 className="text-foreground min-w-0 text-sm font-semibold">
				Permission rules for{' '}
				<TruncateWithTooltip inline dir="auto" className="max-w-[32ch]">
					{credentialName}
				</TruncateWithTooltip>
			</h3>
			{after && <span className="ml-auto">{after}</span>}
		</div>
	);
}

export interface AccessReadViewProps {
	credentialName: string;
	/** The rules the broker applies (an attached rule set's, else the inline ones). */
	rules: readonly PermissionRule[];
	catalog: ActionCatalog;
	/** Null when the API isn't imported: there are no actions to list. */
	apiReference: OpsApiReference | null;
	/** The Edit access verb; absent when the viewer may not change the rules. */
	onEdit?: () => void;
	/** Why the rules can't be changed here, when they can't. */
	lockedReason?: string | null;
	/** The attached shared rule set, which decides instead of the inline rules. */
	ruleSetName?: string | null;
	/** The attached set itself (read-only, with Detach), in place. */
	ruleSetPanel?: ReactNode;
	/** Platform-managed safety rules on this binding. */
	systemRuleCount: number;
	/**
	 * Action-rule ids the API's live version no longer has. Such a rule matches
	 * nothing, so a call it denied or held falls through to the rules after it —
	 * the one thing a reader of the saved rules must not miss.
	 */
	staleActions: number;
	/** The last save just landed (shown for a few seconds, then cleared by the host). */
	justSaved: boolean;
}

export function AccessReadView({
	credentialName,
	rules,
	catalog,
	apiReference,
	onEdit,
	lockedReason,
	ruleSetName,
	ruleSetPanel,
	systemRuleCount,
	staleActions,
	justSaved,
}: AccessReadViewProps) {
	const one = staleActions === 1;
	return (
		<div className="space-y-4" data-testid="access-read-view">
			{staleActions > 0 && (
				<p
					role="status"
					className="bg-warning/10 text-foreground flex items-start gap-2 rounded-lg px-3 py-2.5 text-xs"
					data-testid="access-stale-actions"
				>
					<AlertTriangle
						className="text-warning mt-0.5 h-3.5 w-3.5 shrink-0"
						aria-hidden="true"
					/>
					<span>
						<strong>
							{actionCount(staleActions)} in these rules {one ? 'is' : 'are'} not in
							this version of the API.
						</strong>{' '}
						A rule names an action by an id that changes when the API is re-imported, so{' '}
						{one ? 'that rule decides' : 'those rules decide'} nothing now — a call{' '}
						{one ? 'it' : 'they'} denied or held falls through to the rules below. Open
						Edit access and set {one ? 'it' : 'them'} again.
					</span>
				</p>
			)}
			{lockedReason && (
				<p
					className="bg-surface-inset text-foreground-sub flex items-start gap-2 rounded-lg px-3 py-2.5 text-xs"
					data-testid="access-view-only"
				>
					<Lock className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
					<span>
						<strong className="text-foreground">View only.</strong> {lockedReason}
					</span>
				</p>
			)}
			<AccessHeading
				credentialName={credentialName}
				after={
					// Always mounted, so the announcement is heard; the host clears
					// `justSaved` after a few seconds. (The rise is stilled for reduced motion
					// by the global reset.)
					<span aria-live="polite" className="inline-flex">
						{justSaved && (
							<span
								className="text-foreground-sub animate-rise inline-flex items-center gap-1 text-xs"
								data-testid="rules-saved"
							>
								<Check className="text-success h-3.5 w-3.5" aria-hidden="true" />
								Saved — the broker applies them within {RULE_CACHE_SECONDS} seconds
							</span>
						)}
					</span>
				}
			/>
			<AccessSummary
				rules={rules}
				catalog={catalog}
				apiReference={apiReference}
				source="saved"
				prefix={ruleSetName ? `Shared rule set “${ruleSetName}”. ` : undefined}
				action={
					onEdit && (
						<Button size="sm" variant="secondary" onClick={onEdit}>
							<SlidersHorizontal className="h-4 w-4" aria-hidden="true" /> Edit access
						</Button>
					)
				}
			/>
			{ruleSetPanel}
			<EffectiveAccess
				apiReference={apiReference}
				rules={rules}
				source="saved"
				systemRuleCount={systemRuleCount}
			/>
		</div>
	);
}

/** What the rules (saved, or the draft) let this agent reach against the
 * API's real operations, grouped by path — the bar above carries the totals —
 * and the platform's safety rules that also apply. */
export function EffectiveAccess({
	apiReference,
	rules,
	source,
	systemRuleCount,
}: {
	apiReference: OpsApiReference | null;
	rules: readonly PermissionRule[];
	source: 'saved' | 'draft';
	systemRuleCount: number;
}) {
	return (
		<>
			{apiReference && (
				<OperationImpactPreview
					api={apiReference}
					rules={rules}
					showTotals={false}
					label={
						<>
							Effective access for this binding
							<span className="text-foreground-faint ml-1.5 font-normal">
								{source === 'draft' ? 'your draft' : 'saved'}
							</span>
						</>
					}
				/>
			)}
			{systemRuleCount > 0 && (
				<p className="text-foreground-sub flex items-start gap-1.5 text-xs">
					<ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
					<span>
						{systemRuleCount === 1
							? '1 system safety rule also applies. The platform manages it.'
							: `${systemRuleCount} system safety rules also apply. The platform manages them.`}
					</span>
				</p>
			)}
		</>
	);
}
