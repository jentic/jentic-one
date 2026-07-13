import { useState } from 'react';
import { Save } from 'lucide-react';
import { Button } from '@/shared/ui';
import { useReplacePermissions } from '@/modules/toolkits/api';
import {
	PermissionRuleEditor,
	isEmptyAllowRule,
} from '@/modules/toolkits/components/PermissionRuleEditor';
import type { PermissionRule, PermissionRuleInput } from '@/modules/toolkits/api/types';

/**
 * Inline editor for the agent permission rules on one toolkit↔credential
 * binding. System safety rules (`_system: true`, appended by the backend on
 * every save) are filtered out of the editor so saving never persists them as
 * agent rules — the backend re-appends a fresh copy each time.
 */
export interface CredentialPermissionEditorProps {
	toolkitId: string;
	credentialId: string;
	credentialLabel: string;
	initialRules: PermissionRule[];
	onClose: () => void;
}

function toInput(rule: PermissionRule): PermissionRuleInput {
	// `effect` is two distinct generated string enums (read vs write schema) with
	// identical 'allow'/'deny' values; TS treats string-enum members as assignable
	// across them, so copying directly is type-safe (verified under `strict`).
	return {
		effect: rule.effect,
		methods: rule.methods ?? undefined,
		path: rule.path ?? undefined,
		operations: rule.operations ?? undefined,
	};
}

export function CredentialPermissionEditor({
	toolkitId,
	credentialId,
	credentialLabel,
	initialRules,
	onClose,
}: CredentialPermissionEditorProps) {
	const [rules, setRules] = useState<PermissionRuleInput[]>(() =>
		initialRules.filter((r) => !r._system).map(toInput),
	);
	const replace = useReplacePermissions(toolkitId, credentialId);

	const clean = rules.map((rule) => {
		const out: PermissionRuleInput = { effect: rule.effect };
		if (Array.isArray(rule.methods) && rule.methods.length > 0) out.methods = rule.methods;
		if (typeof rule.path === 'string' && rule.path.trim() !== '') out.path = rule.path.trim();
		if (Array.isArray(rule.operations) && rule.operations.length > 0)
			out.operations = rule.operations;
		return out;
	});
	// A condition-less `allow` is rejected by the backend (422). Block save and
	// rely on the editor's inline warning rather than submitting a known error.
	const hasInvalidRule = clean.some(isEmptyAllowRule);

	const save = () => {
		if (hasInvalidRule) return;
		replace.mutate(clean, { onSuccess: () => onClose() });
	};

	return (
		<div className="border-border bg-muted/20 space-y-4 border-t p-4 sm:p-5">
			<div className="flex items-start justify-between gap-2">
				<div>
					<p className="text-foreground text-sm font-semibold">
						Permission Rules for {credentialLabel}
					</p>
					<p className="text-muted-foreground mt-0.5 text-xs">
						Define which operations this credential can access. With no rules, all
						operations are denied. System safety rules are always appended.
					</p>
				</div>
				<Button variant="ghost" size="icon" onClick={onClose} aria-label="Close">
					×
				</Button>
			</div>

			<PermissionRuleEditor rules={rules} onChange={setRules} />

			<div className="flex gap-2 pt-2">
				<Button onClick={save} loading={replace.isPending} disabled={hasInvalidRule}>
					<Save className="h-4 w-4" /> {replace.isPending ? 'Saving...' : 'Save Rules'}
				</Button>
				<Button variant="secondary" onClick={onClose}>
					Cancel
				</Button>
			</div>
		</div>
	);
}
