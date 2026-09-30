import { useEffect, useRef, useState } from 'react';
import { Plus, X } from 'lucide-react';
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
 *
 * One row does the asking (method · path · Test); the optional operation id is a
 * disclosure, and collapsing it clears the value — a hidden field must never
 * influence a verdict.
 */

const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'] as const;

export interface AgentBindingRuleTesterProps {
	agentId: string;
	credentialId: string;
	/** The binding's SAVED rules (system rows included), for naming the match. */
	savedRules: BindingPermissionRule[];
	/** Disable the tester while the host's editor holds an unsaved draft — the
	 * dry-run evaluates SAVED rules, so a verdict against a stale set would
	 * mislead. The caption names the reason. */
	disabled?: boolean;
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

/** Shared chip shell so allow and deny read as the same kind of answer. */
function VerdictChip({ allowed, children }: { allowed: boolean; children: React.ReactNode }) {
	return (
		<p
			className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs"
			data-testid="rule-verdict"
		>
			<span
				className={
					allowed
						? 'bg-success/15 text-success rounded-md px-2 py-0.5 text-xs font-semibold'
						: 'bg-danger/15 text-danger rounded-md px-2 py-0.5 text-xs font-semibold'
				}
			>
				{allowed ? 'Allowed' : 'Denied'}
			</span>{' '}
			<span className="text-muted-foreground min-w-0">{children}</span>
		</p>
	);
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
			) : anchor != null ? (
				<>
					— matched rule{' '}
					<span className="text-foreground font-mono font-semibold">{anchor}</span>
					{summary ? <> · {summary}</> : null}
				</>
			) : (
				<>— matched a {effectWord} rule on this binding</>
			)}
		</VerdictChip>
	);
}

export function AgentBindingRuleTester({
	agentId,
	credentialId,
	savedRules,
	disabled = false,
}: AgentBindingRuleTesterProps) {
	const [method, setMethod] = useState<string>('GET');
	const [path, setPath] = useState('');
	const [operationId, setOperationId] = useState('');
	// The disclosure opens itself whenever it holds a value; closing clears it.
	const [operationOpen, setOperationOpen] = useState(false);
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

	const run = () => {
		if (disabled) return;
		const trimmed = path.trim();
		if (!trimmed) return;
		const op = operationId.trim();
		test.mutate({ method, path: trimmed, ...(op ? { operation_id: op } : {}) });
	};

	return (
		<div className="border-border/60 bg-card space-y-2 rounded-lg border border-dashed p-3">
			<div className="flex items-center gap-2">
				<div className="w-24 shrink-0">
					<Select
						aria-label="HTTP method"
						value={method}
						onChange={(e) => setMethod(e.target.value)}
						className="px-2 py-1.5 text-xs"
						disabled={disabled}
					>
						{METHODS.map((m) => (
							<option key={m} value={m}>
								{m}
							</option>
						))}
					</Select>
				</div>
				<div className="min-w-0 flex-1">
					<Input
						aria-label="Request path"
						value={path}
						onChange={(e) => setPath(e.target.value)}
						placeholder="/repos/acme/site/issues"
						className="px-2.5 py-1.5 font-mono text-xs"
						disabled={disabled}
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
					disabled={disabled || !path.trim()}
				>
					Test
				</Button>
			</div>

			{/* Operation-scoped rules only fire when the request carries an operation id,
			    which would otherwise always dry-run to default-deny. */}
			{operationOpen && (
				<div className="flex items-center gap-2">
					<div className="min-w-0 flex-1">
						<Input
							aria-label="Operation ID (optional)"
							value={operationId}
							onChange={(e) => setOperationId(e.target.value)}
							placeholder="operationId"
							className="px-2.5 py-1.5 font-mono text-xs"
							disabled={disabled}
							autoFocus
							onKeyDown={(e) => {
								if (e.key === 'Enter' && !test.isPending) run();
							}}
						/>
					</div>
					<Button
						variant="ghost"
						size="icon"
						aria-label="Remove operation id"
						disabled={disabled}
						onClick={() => {
							// Clear as well as hide: an invisible value must not change the next verdict.
							setOperationId('');
							setOperationOpen(false);
						}}
					>
						<X className="h-4 w-4" />
					</Button>
				</div>
			)}

			{test.isError && (
				<p className="text-danger text-xs">
					{test.error instanceof Error ? test.error.message : 'Test failed.'}
				</p>
			)}
			{!disabled && test.data && <Verdict result={test.data} savedRules={savedRules} />}

			<div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
				{disabled ? (
					<p className="text-warning text-xs" data-testid="rule-tester-disabled-note">
						Paused while the editor holds unsaved changes — the dry-run evaluates the{' '}
						<strong>saved</strong> rules only.
					</p>
				) : (
					<p className="text-muted-foreground text-xs">
						Dry-runs the broker's decision against the <strong>saved</strong> rules.
						Nothing is sent upstream.
					</p>
				)}
				{!operationOpen && (
					<Button
						variant="ghost"
						size="sm"
						disabled={disabled}
						onClick={() => setOperationOpen(true)}
						className="text-muted-foreground hover:text-foreground h-auto shrink-0 px-1.5 py-0.5 text-xs"
					>
						<Plus className="h-3 w-3" /> operation id
					</Button>
				)}
			</div>
		</div>
	);
}
