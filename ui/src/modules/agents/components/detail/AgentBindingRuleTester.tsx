import { useEffect, useMemo, useRef, useState } from 'react';
import { ruleSummary } from '@/shared/lib';
import {
	useTestAgentBindingPermissions,
	type BindingPermissionRule,
	type BindingPermissionTestResult,
} from '@/modules/agents/api';
import { toDisplayRules } from '@/modules/agents/components/detail/shared';
import { useVendorOperationsProgress } from '@/shared/credentials/api/vendors-hooks';
import type { VendorOperation } from '@/shared/credentials/api/vendors-client';
import { operationForRequest } from '@/shared/credentials/lib/template-matcher';
import {
	type OpsApiReference,
	MatchedRuleText,
	RuleRequestTester,
	VerdictChip,
	verdictOutcome,
} from '@/shared/credentials/components';

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
 * Action rules name operations by id, and the broker resolves every request
 * to its operation before it evaluates them. The dry run does the same: the
 * method and path are matched to the API's operation and its id goes with the
 * request, unless the operator typed one. Without that, a By action deny
 * would never show in a test.
 *
 * The controls are the shared `RuleRequestTester`; this binds them to the
 * broker's dry run.
 */

export interface AgentBindingRuleTesterProps {
	agentId: string;
	credentialId: string;
	/** The binding's SAVED rules (system rows included), for naming the match. */
	savedRules: BindingPermissionRule[];
	/** The host's editor holds unsaved changes: the tester still runs (on the
	 * SAVED rules) and its caption says the draft isn't what it checks. */
	draftPending?: boolean;
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
	operation,
}: {
	result: BindingPermissionTestResult;
	savedRules: BindingPermissionRule[];
	/** The API operation the request was read as, when the path resolved to one. */
	operation: VendorOperation | null;
}) {
	const asOperation = operation ? (
		<span data-testid="rule-tester-operation">
			{' '}
			· as {operation.method.toUpperCase()} {operation.path}
		</span>
	) : null;
	if (!result.matched) {
		return (
			<VerdictChip outcome="denied">
				— no rule matched (default deny){asOperation}
			</VerdictChip>
		);
	}
	const outcome = verdictOutcome(result.effect);
	const { anchor, summary } = resolveMatch(result, savedRules);
	const effectWord =
		outcome === 'allowed' ? 'an allow' : outcome === 'approval' ? 'an ask' : 'a deny';
	return (
		<VerdictChip outcome={outcome}>
			{result.is_system ? (
				<>— matched a platform system safety rule</>
			) : (
				<MatchedRuleText
					anchor={anchor}
					summary={summary}
					fallback={<>— matched {effectWord} rule on this binding</>}
				/>
			)}
			{asOperation}
		</VerdictChip>
	);
}

export function AgentBindingRuleTester({
	agentId,
	credentialId,
	savedRules,
	draftPending = false,
	apiReference,
}: AgentBindingRuleTesterProps) {
	// The whole operation list, off the same cache as By action and Effective
	// access: a request must resolve to any operation, not just the first page's.
	const { operations } = useVendorOperationsProgress(apiReference ?? undefined, {
		enabled: !!apiReference,
	});
	const paths = useMemo(() => operations.map((op) => op.path), [operations]);
	const test = useTestAgentBindingPermissions(agentId, credentialId);
	const { reset: resetVerdict } = test;
	/** The operation the last run's path resolved to (none when an id was typed). */
	const [operation, setOperation] = useState<VendorOperation | null>(null);

	// A verdict speaks only for the rules it was run against, and an always-mounted
	// host keeps this tester alive across saves. So drop it when the saved rules
	// change CONTENT (compared by value).
	const savedRulesFingerprint = JSON.stringify(savedRules);
	const verdictContext = useRef(savedRulesFingerprint);
	useEffect(() => {
		const prev = verdictContext.current;
		verdictContext.current = savedRulesFingerprint;
		if (savedRulesFingerprint !== prev) resetVerdict();
	}, [savedRulesFingerprint, resetVerdict]);

	return (
		<RuleRequestTester
			onRun={(request) => {
				// A typed id wins; else the path is read as the API's operation, so
				// the rules on that operation are checked as the broker checks them.
				const op = request.operation_id
					? null
					: operationForRequest(operations, request.method, request.path);
				setOperation(op);
				test.mutate(op ? { ...request, operation_id: op.operation_id } : request);
			}}
			pending={test.isPending}
			error={
				test.isError
					? test.error instanceof Error
						? test.error.message
						: 'Test failed.'
					: null
			}
			verdict={
				test.data ? (
					<Verdict result={test.data} savedRules={savedRules} operation={operation} />
				) : null
			}
			paths={paths}
			note={
				<p className="text-foreground-sub text-xs" data-testid="rule-tester-note">
					Checks the <strong>saved</strong> rules, action rules included — the path is
					read as the API's operation. Nothing is sent upstream
					{draftPending ? ' — save to test your changes.' : '.'}
				</p>
			}
		/>
	);
}
