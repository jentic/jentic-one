import { useState } from 'react';
import { Button, Input, Select } from '@/shared/ui';
import { useTestPermissions } from '@/modules/toolkits/api';
import type { PermissionTestResult } from '@/modules/toolkits/api/types';

/**
 * Rule tester — the broker's own dry-run (`POST …/permissions:test`) surfaced
 * next to the rule editor, so authoring becomes write→test→save instead of
 * write-and-pray. Rendered headless (the host's disclosure carries the "Test a
 * request" title).
 *
 * The verdict evaluates the SAVED rules (the same vendor-pooled list the
 * broker sees at request time), not the editor's unsaved draft — the caption
 * says so. Vendor pooling also means the matching rule may come from a
 * *different* same-vendor binding; when the response says so, the verdict
 * names it.
 */

const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'] as const;

export interface RuleTesterProps {
	toolkitId: string;
	credentialId: string;
}

function Verdict({ result, credentialId }: { result: PermissionTestResult; credentialId: string }) {
	const pooled = result.credential_id != null && result.credential_id !== credentialId;
	if (!result.matched) {
		return (
			<p className="text-danger flex items-center gap-1.5 text-xs" data-testid="rule-verdict">
				<span className="bg-danger h-2 w-2 shrink-0 rounded-full" aria-hidden="true" />
				Denied — no rule matched (default deny)
			</p>
		);
	}
	const allowed = result.allowed;
	return (
		<p
			className={`flex items-center gap-1.5 text-xs ${allowed ? 'text-success' : 'text-danger'}`}
			data-testid="rule-verdict"
		>
			<span
				className={`h-2 w-2 shrink-0 rounded-full ${allowed ? 'bg-success' : 'bg-danger'}`}
				aria-hidden="true"
			/>
			{allowed ? 'Allowed' : 'Denied'} — matched rule #{(result.rule_index ?? 0) + 1}
			{result.is_system ? ' (system safety)' : ''}
			{pooled ? (
				<span className="text-muted-foreground">
					via same-vendor binding{' '}
					<span className="font-mono">{result.credential_id}</span>
				</span>
			) : null}
		</p>
	);
}

export function RuleTester({ toolkitId, credentialId }: RuleTesterProps) {
	const [method, setMethod] = useState<string>('GET');
	const [path, setPath] = useState('');
	const test = useTestPermissions(toolkitId, credentialId);

	const run = () => {
		const trimmed = path.trim();
		if (!trimmed) return;
		test.mutate({ method, path: trimmed });
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
			{test.data && <Verdict result={test.data} credentialId={credentialId} />}
			<p className="text-muted-foreground text-xs">
				Dry-runs the broker's decision against the <strong>saved</strong> rules — save your
				draft first, then verify here. Nothing is sent upstream.
			</p>
		</div>
	);
}
