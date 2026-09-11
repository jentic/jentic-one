import { useState } from 'react';
import { Button, Input, Select } from '@/shared/ui';
import { ruleSummary } from '@/shared/lib';
import {
	useTestAgentBindingPermissions,
	type BindingPermissionRule,
	type BindingPermissionTestResult,
} from '@/modules/agents/api';
import { toDisplayRules } from '@/modules/agents/components/detail/shared';

/**
 * Rule tester for one direct agent↔credential binding — the broker's own
 * dry-run (`POST /credentials/{cid}/agents/{aid}/permissions:test`) surfaced
 * next to the rule editor, so authoring becomes write→test→save instead of
 * write-and-pray. Rendered headless (the host's disclosure carries the "Test
 * a request" title). Transplanted from the toolkit rule tester, minus its
 * vendor-pooling disambiguation: the direct `:test` evaluates exactly this
 * binding's ordered rules, so a matched user rule always anchors to the same
 * `#N` the editor rows carry.
 *
 * The verdict evaluates the SAVED rules (what the broker sees at request
 * time), not the editor's unsaved draft — the caption says so.
 */

const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'] as const;

export interface AgentBindingRuleTesterProps {
	agentId: string;
	credentialId: string;
	/** The binding's SAVED rules (system rows included), for naming the match. */
	savedRules: BindingPermissionRule[];
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
		return (
			<p className="text-danger flex items-center gap-1.5 text-xs" data-testid="rule-verdict">
				<span className="bg-danger h-2 w-2 shrink-0 rounded-full" aria-hidden="true" />
				Denied — no rule matched (default deny)
			</p>
		);
	}
	const allowed = result.allowed;
	const { anchor, summary } = resolveMatch(result, savedRules);
	const effectWord = allowed ? 'allow' : 'deny';
	return (
		<p
			className={`flex flex-wrap items-center gap-1.5 text-xs ${allowed ? 'text-success' : 'text-danger'}`}
			data-testid="rule-verdict"
		>
			<span
				className={`h-2 w-2 shrink-0 rounded-full ${allowed ? 'bg-success' : 'bg-danger'}`}
				aria-hidden="true"
			/>
			{allowed ? 'Allowed' : 'Denied'}
			{result.is_system ? (
				<> — matched a platform system safety rule</>
			) : anchor != null ? (
				<>
					{' '}
					— matched rule <span className="font-mono font-semibold">{anchor}</span>
					{summary ? <span className="text-muted-foreground">· {summary}</span> : null}
				</>
			) : (
				<> — matched a {effectWord} rule on this binding</>
			)}
		</p>
	);
}

export function AgentBindingRuleTester({
	agentId,
	credentialId,
	savedRules,
}: AgentBindingRuleTesterProps) {
	const [method, setMethod] = useState<string>('GET');
	const [path, setPath] = useState('');
	const [operationId, setOperationId] = useState('');
	const test = useTestAgentBindingPermissions(agentId, credentialId);

	const run = () => {
		const trimmed = path.trim();
		if (!trimmed) return;
		const op = operationId.trim();
		test.mutate({ method, path: trimmed, ...(op ? { operation_id: op } : {}) });
	};

	return (
		<div className="border-border/60 bg-card space-y-2 rounded-lg border border-dashed p-3">
			<div className="flex flex-wrap items-center gap-2">
				<div className="w-28 shrink-0">
					<Select
						aria-label="HTTP method"
						value={method}
						onChange={(e) => setMethod(e.target.value)}
						className="text-xs"
					>
						{METHODS.map((m) => (
							<option key={m} value={m}>
								{m}
							</option>
						))}
					</Select>
				</div>
				<div className="min-w-40 flex-1">
					<Input
						aria-label="Request path"
						value={path}
						onChange={(e) => setPath(e.target.value)}
						placeholder="/repos/acme/site/issues"
						className="font-mono text-xs"
						onKeyDown={(e) => {
							if (e.key === 'Enter' && !test.isPending) run();
						}}
					/>
				</div>
				{/* Operation-scoped rules only fire when the request carries an
				    operation id — without this input, a binding whose grants are
				    operation-based would always dry-run to default-deny. */}
				<div className="w-44 shrink-0">
					<Input
						aria-label="Operation ID (optional)"
						value={operationId}
						onChange={(e) => setOperationId(e.target.value)}
						placeholder="operationId (optional)"
						className="font-mono text-xs"
						onKeyDown={(e) => {
							if (e.key === 'Enter' && !test.isPending) run();
						}}
					/>
				</div>
				<Button
					variant="secondary"
					size="sm"
					onClick={run}
					loading={test.isPending}
					disabled={!path.trim()}
				>
					Test
				</Button>
			</div>
			{test.isError && (
				<p className="text-danger text-xs">
					{test.error instanceof Error ? test.error.message : 'Test failed.'}
				</p>
			)}
			{test.data && <Verdict result={test.data} savedRules={savedRules} />}
			<p className="text-muted-foreground text-xs">
				Dry-runs the broker's decision against the <strong>saved</strong> rules — save your
				draft first, then verify here. Rule numbers match the editor above. Nothing is sent
				upstream.
			</p>
		</div>
	);
}
