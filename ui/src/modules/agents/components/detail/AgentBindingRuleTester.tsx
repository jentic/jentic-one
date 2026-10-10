import { useEffect, useMemo, useRef } from 'react';
import { PauseCircle } from 'lucide-react';
import { ruleSummary } from '@/shared/lib';
import {
	useTestAgentBindingPermissions,
	type BindingPermissionRule,
	type BindingPermissionTestResult,
} from '@/modules/agents/api';
import { toDisplayRules } from '@/modules/agents/components/detail/shared';
import { useVendorOperations } from '@/shared/credentials/api/vendors-hooks';
import type { OpsApiReference } from '@/shared/credentials/components/OperationImpactPreview';
import {
	MatchedRuleText,
	RuleRequestTester,
	VerdictChip,
} from '@/shared/credentials/components/RuleRequestTester';

/**
 * Rule tester for one direct agent↔credential binding — the broker's own
 * dry-run (`POST /credentials/{cid}/agents/{aid}/permissions:test`) surfaced
 * next to the rule editor, so authoring becomes write→test→save instead of
 * write-and-pray. Rendered headless (the host's disclosure carries the "Test
 * a request" title). The direct `:test` evaluates exactly this binding's
 * ordered rules, so a matched user rule always anchors to the same `#N` the
 * editor rows carry.
 *
 * The verdict evaluates the SAVED rules (what the broker sees at request
 * time), not the editor's unsaved draft — the caption says so.
 *
 * The controls are the shared `RuleRequestTester`; this binds them to the
 * broker's dry run.
 */

export interface AgentBindingRuleTesterProps {
	agentId: string;
	credentialId: string;
	/** The binding's SAVED rules (system rows included), for naming the match. */
	savedRules: BindingPermissionRule[];
	/** Disable the tester while the host's editor holds an unsaved draft — the
	 * dry-run evaluates SAVED rules, so a verdict against a stale set would
	 * mislead. The caption names the reason. */
	disabled?: boolean;
	/** The API the binding covers — its real paths seed the path placeholder. */
	apiReference?: OpsApiReference | null;
}

/** The matched rule resolved to the editor's visible numbering, when possible. */
function resolveMatch(
	result: BindingPermissionTestResult,
	savedRules: BindingPermissionRule[],
): { anchor: string | null; summary: string | null } {
	if (!result.matched || result.rule_index == null || result.is_system) {
		return { anchor: null, summary: null };
	}
	// The direct pool is this binding's own ordered list. The editor shows
	// only user rules; its row numbers map 1:1 onto the pooled index as long
	// as every system rule sits AFTER the user rules (their platform-appended
	// position). Any other shape: name the rule by content only.
	const firstSystem = savedRules.findIndex((r) => r._system);
	const userFirstOrder =
		firstSystem === -1 || savedRules.slice(firstSystem).every((r) => r._system);
	const matched = savedRules[result.rule_index];
	if (!matched || matched._system) return { anchor: null, summary: null };
	return {
		anchor: userFirstOrder ? `#${result.rule_index + 1}` : null,
		summary: ruleSummary(toDisplayRules([matched])).replace(/\.$/, ''),
	};
}

function Verdict({
	result,
	savedRules,
}: {
	result: BindingPermissionTestResult;
	savedRules: BindingPermissionRule[];
}) {
	if (!result.matched) {
		return <VerdictChip allowed={false}>— no rule matched (default deny)</VerdictChip>;
	}
	const allowed = result.allowed;
	const { anchor, summary } = resolveMatch(result, savedRules);
	const effectWord = allowed ? 'allow' : 'deny';
	return (
		<VerdictChip allowed={allowed}>
			{result.is_system ? (
				<>— matched a platform system safety rule</>
			) : (
				<MatchedRuleText
					anchor={anchor}
					summary={summary}
					fallback={<>— matched a {effectWord} rule on this binding</>}
				/>
			)}
		</VerdictChip>
	);
}

export function AgentBindingRuleTester({
	agentId,
	credentialId,
	savedRules,
	disabled = false,
	apiReference,
}: AgentBindingRuleTesterProps) {
	// Same query key as the rule editor's suggestions, so this reads the cache.
	const opsQuery = useVendorOperations(apiReference ?? undefined, { enabled: !!apiReference });
	const paths = useMemo(() => opsQuery.data?.data?.map((op) => op.path), [opsQuery.data]);
	const test = useTestAgentBindingPermissions(agentId, credentialId);
	const { reset: resetVerdict } = test;

	// A verdict speaks only for the rules it was run against, and an always-mounted
	// host keeps this tester alive across saves. So drop it when the saved rules
	// change CONTENT (compared by value) or when an edit session ends.
	const savedRulesFingerprint = JSON.stringify(savedRules);
	const verdictContext = useRef({ savedRulesFingerprint, disabled });
	useEffect(() => {
		const prev = verdictContext.current;
		verdictContext.current = { savedRulesFingerprint, disabled };
		if (savedRulesFingerprint !== prev.savedRulesFingerprint || (prev.disabled && !disabled)) {
			resetVerdict();
		}
	}, [savedRulesFingerprint, disabled, resetVerdict]);

	return (
		<RuleRequestTester
			onRun={(request) => test.mutate(request)}
			pending={test.isPending}
			error={
				test.isError
					? test.error instanceof Error
						? test.error.message
						: 'Test failed.'
					: null
			}
			verdict={test.data ? <Verdict result={test.data} savedRules={savedRules} /> : null}
			disabled={disabled}
			paths={paths}
			note={
				disabled ? (
					<p
						className="text-foreground-sub text-xs"
						data-testid="rule-tester-disabled-note"
					>
						<PauseCircle
							className="text-caution -mt-px mr-1 inline h-3.5 w-3.5 align-middle"
							aria-hidden="true"
						/>
						Paused while the editor holds unsaved changes — the dry-run evaluates the{' '}
						<strong>saved</strong> rules only.
					</p>
				) : (
					<p className="text-foreground-sub text-xs">
						Dry-runs the broker's decision against the <strong>saved</strong> rules.
						Nothing is sent upstream.
					</p>
				)
			}
		/>
	);
}
