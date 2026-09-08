import { useState } from 'react';
import { Button, Input, Select } from '@/shared/ui';
import { ruleSummary } from '@/shared/lib';
import { useTestPermissions, useToolkitBindings } from '@/modules/toolkits/api';
import { toDisplayRules } from '@/modules/toolkits/components/detail/shared';
import type { PermissionTestResult, ToolkitCredentialBinding } from '@/modules/toolkits/api/types';

/**
 * Rule tester — the broker's own dry-run (`POST …/permissions:test`) surfaced
 * next to the rule editor, so authoring becomes write→test→save instead of
 * write-and-pray. Rendered headless (the host's disclosure carries the "Test a
 * request" title).
 *
 * The verdict evaluates the SAVED rules (the same vendor-pooled list the
 * broker sees at request time), not the editor's unsaved draft — the caption
 * says so.
 *
 * Anchoring the verdict: the response's `rule_index` points into the broker's
 * vendor-POOLED list (all same-vendor bindings' rules interleaved by an
 * internal sequence, system rules included) — an ordinal the UI can't render
 * as-is because the editor shows only this binding's agent rules. The verdict
 * therefore names the matched rule by CONTENT (`ruleSummary`) and only cites
 * "#N" when the mapping is unambiguous: the match came from this binding, is
 * not a system rule, and no other same-vendor binding contributes rules to the
 * pool — exactly then pooled order equals this binding's visible rule order,
 * and #N matches the numbers on the editor rows.
 */

const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'] as const;

export interface RuleTesterProps {
	toolkitId: string;
	credentialId: string;
}

/** The matched rule resolved to something the operator can see, when possible. */
function resolveMatch(
	result: PermissionTestResult,
	credentialId: string,
	bindings: ToolkitCredentialBinding[],
): { anchor: string | null; summary: string | null; pooledFrom: string | null } {
	if (!result.matched || result.rule_index == null) {
		return { anchor: null, summary: null, pooledFrom: null };
	}
	const mine = bindings.find((b) => b.credential_id === credentialId);
	// Match contributed by ANOTHER same-vendor binding: name it instead of
	// citing an index into a list the user cannot see.
	if (result.credential_id != null && result.credential_id !== credentialId) {
		const other = bindings.find((b) => b.credential_id === result.credential_id);
		return {
			anchor: null,
			summary: null,
			pooledFrom: other?.label ?? result.credential_id,
		};
	}
	if (result.is_system) return { anchor: null, summary: null, pooledFrom: null };
	if (!mine) return { anchor: null, summary: null, pooledFrom: null };
	// Pooled index maps 1:1 onto this binding's visible rows only when the pool
	// has a single contributing binding AND every system rule sits after the
	// user rules (their platform-appended position) — then user-rule index i in
	// the pool is row #i+1 in the editor. Any other shape: no number.
	const sameVendorCount = bindings.filter(
		(b) => (b.api_vendor ?? null) === (mine.api_vendor ?? null),
	).length;
	const rules = mine.permissions ?? [];
	const firstSystem = rules.findIndex((r) => r._system);
	const userFirstOrder = firstSystem === -1 || rules.slice(firstSystem).every((r) => r._system);
	if (sameVendorCount > 1 || !userFirstOrder)
		return { anchor: null, summary: null, pooledFrom: null };
	const matched = rules[result.rule_index];
	if (!matched || matched._system) return { anchor: null, summary: null, pooledFrom: null };
	return {
		anchor: `#${result.rule_index + 1}`,
		summary: ruleSummary(toDisplayRules([matched])).replace(/\.$/, ''),
		pooledFrom: null,
	};
}

function Verdict({
	result,
	credentialId,
	bindings,
}: {
	result: PermissionTestResult;
	credentialId: string;
	bindings: ToolkitCredentialBinding[];
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
	const { anchor, summary, pooledFrom } = resolveMatch(result, credentialId, bindings);
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
			) : pooledFrom != null ? (
				<>
					{' '}
					— matched a {effectWord} rule on same-vendor binding{' '}
					<span className="font-medium">{pooledFrom}</span>
				</>
			) : anchor != null ? (
				<>
					{' '}
					— matched rule <span className="font-mono font-semibold">{anchor}</span>
					{summary ? <span className="text-muted-foreground">· {summary}</span> : null}
				</>
			) : (
				<> — matched a {effectWord} rule in this toolkit's rule pool</>
			)}
		</p>
	);
}

export function RuleTester({ toolkitId, credentialId }: RuleTesterProps) {
	const [method, setMethod] = useState<string>('GET');
	const [path, setPath] = useState('');
	const [operationId, setOperationId] = useState('');
	const { data: bindings = [] } = useToolkitBindings(toolkitId);
	const test = useTestPermissions(toolkitId, credentialId);

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
				    operation id — without this input, a toolkit whose grants are
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
			{test.data && (
				<Verdict result={test.data} credentialId={credentialId} bindings={bindings} />
			)}
			<p className="text-muted-foreground text-xs">
				Dry-runs the broker's decision against the <strong>saved</strong> rules — save your
				draft first, then verify here. Rule numbers match the editor above. Nothing is sent
				upstream.
			</p>
		</div>
	);
}
