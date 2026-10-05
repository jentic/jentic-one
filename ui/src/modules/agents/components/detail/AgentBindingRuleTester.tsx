import { useEffect, useMemo, useRef, useState } from 'react';
import { PauseCircle, Plus, X } from 'lucide-react';
import { Button, Input, Select } from '@/shared/ui';
import { ruleSummary } from '@/shared/lib';
import {
	useTestAgentBindingPermissions,
	type BindingPermissionRule,
	type BindingPermissionTestResult,
} from '@/modules/agents/api';
import { toDisplayRules } from '@/modules/agents/components/detail/shared';
import { useVendorOperations } from '@/shared/credentials/api/vendors-hooks';
import type { OpsApiReference } from '@/shared/credentials/components/OperationImpactPreview';
import { examplePath } from '@/shared/credentials/lib/path-completion';

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

/** Shared chip shell so allow and deny read as the same kind of answer. */
function VerdictChip({ allowed, children }: { allowed: boolean; children: React.ReactNode }) {
	return (
		<p
			className="bg-surface-sheet flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md px-2.5 py-2 text-xs"
			data-testid="rule-verdict"
		>
			<span
				className={
					allowed
						? 'bg-success/15 text-success rounded-[5px] px-2 py-0.5 text-xs font-semibold'
						: 'bg-danger/15 text-danger rounded-[5px] px-2 py-0.5 text-xs font-semibold'
				}
			>
				{allowed ? 'Allowed' : 'Denied'}
			</span>{' '}
			<span className="text-foreground-sub min-w-0">{children}</span>
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
	apiReference,
}: AgentBindingRuleTesterProps) {
	// Same query key as the rule editor's suggestions, so this reads the cache.
	const opsQuery = useVendorOperations(apiReference ?? undefined, { enabled: !!apiReference });
	const pathPlaceholder = useMemo(
		() => examplePath(opsQuery.data?.data?.map((op) => op.path)),
		[opsQuery.data],
	);
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
		// Same borderless card as the rule editor above; only the controls carry
		// an edge (`.edged-controls`).
		<div className="bg-surface-inset edged-controls space-y-2.5 rounded-lg p-3 sm:p-4">
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
						placeholder={pathPlaceholder}
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
					className="shrink-0"
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
				<p className="text-danger text-xs" role="alert">
					{test.error instanceof Error ? test.error.message : 'Test failed.'}
				</p>
			)}
			{!disabled && test.data && <Verdict result={test.data} savedRules={savedRules} />}

			<div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
				{disabled ? (
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
				)}
				{!operationOpen && (
					<Button
						variant="tonal"
						size="xs"
						disabled={disabled}
						onClick={() => setOperationOpen(true)}
						className="shrink-0"
					>
						<Plus className="h-3 w-3" /> operation id
					</Button>
				)}
			</div>
		</div>
	);
}
