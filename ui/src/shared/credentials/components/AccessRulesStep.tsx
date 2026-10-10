/**
 * AccessRulesStep — "What can this agent call?": the access presets (Allow all
 * operations · Read-only (GET only) · Custom rules) as a radio group, the
 * coverage caveat for a credential that reaches beyond one pinned API, and the
 * shared rules editor under Custom. Optionally, an inline "Try a request" that
 * dry-runs the rules as they stand — unsaved — with the broker's semantics.
 *
 * Controlled: the host owns the preset and the custom rules, and turns them
 * into the rules to save with `rulesForPreset`. The workspace bind dialog and
 * the Agents tab's Add-APIs queue both render it, so the choice reads the same
 * wherever a binding is made.
 */
import { useId, useMemo, type ReactNode } from 'react';
import { RadioCardGroup } from '@/shared/ui';
import { useVendorOperations } from '@/shared/credentials/api/vendors-hooks';
import type { PermissionRule } from '@/shared/credentials/api/vendors-types';
import { RuleListEditor } from '@/shared/credentials/components/RuleListEditor';
import { DraftRuleTester } from '@/shared/credentials/components/RuleRequestTester';
import {
	coverageNote,
	draftRulesForPreset,
	presetOptions,
	type RulesPreset,
	type ScopeReach,
} from '@/shared/credentials/lib/accessPresets';

export interface AccessRulesStepProps {
	reach: ScopeReach;
	preset: RulesPreset | null;
	onPresetChange: (preset: RulesPreset) => void;
	customRules: PermissionRule[];
	onCustomRulesChange: (rules: PermissionRule[]) => void;
	/**
	 * The API (with a concrete version) whose operations seed the editor's path
	 * suggestions and the tester's placeholder. Omitted → both work without them.
	 */
	apiReference?: { vendor: string; name: string; version: string } | null;
	/** Fetch the API's operations only while the host is showing this step. */
	active?: boolean;
	disabled?: boolean;
	/** The question above the presets. */
	heading?: ReactNode;
	/** Show the inline "Try a request" dry run against the rules being edited. */
	tester?: boolean;
	/** Prefix for the step's `data-testid`s (`<prefix>-rules`, `<prefix>-rules-preset`,
	 * `<prefix>-coverage-note`, `<prefix>-tester`). */
	testIdPrefix?: string;
}

/** How the inline tester names the rule that decided, for a preset — whose rule
 * is never shown as a numbered editor row. */
const PRESET_MATCH: Record<Exclude<RulesPreset, 'custom'>, string> = {
	all: '— allowed by “Allow all operations”',
	read: '— allowed by “Read-only (GET only)”',
};

export function AccessRulesStep({
	reach,
	preset,
	onPresetChange,
	customRules,
	onCustomRulesChange,
	apiReference,
	active = true,
	disabled = false,
	heading = 'What can this agent call?',
	tester = false,
	testIdPrefix = 'access',
}: AccessRulesStepProps) {
	const headingId = useId();
	const testerId = useId();
	const options = useMemo(() => presetOptions(reach), [reach]);
	const note = coverageNote(reach);

	// The editor's path suggestions (and the tester's placeholder): this API's
	// real operations. Same query key as every other reader, so it is cached.
	const opsQuery = useVendorOperations(apiReference ?? undefined, {
		enabled: active && (preset === 'custom' || tester) && !!apiReference,
	});
	const pathSuggestions = useMemo<readonly string[]>(() => {
		const rows = opsQuery.data?.data;
		if (!rows) return [];
		return Array.from(new Set(rows.map((op) => op.path))).sort();
	}, [opsQuery.data]);

	const draftRules = useMemo(
		() => draftRulesForPreset(preset, customRules),
		[preset, customRules],
	);
	const describeMatch =
		preset === 'all' || preset === 'read' ? () => PRESET_MATCH[preset] : undefined;

	return (
		<div className="space-y-2" data-testid={`${testIdPrefix}-rules`}>
			<p id={headingId} className="text-foreground text-sm font-medium">
				{heading}
			</p>
			<RadioCardGroup
				options={options}
				value={preset}
				onChange={onPresetChange}
				ariaLabelledBy={headingId}
				disabled={disabled}
				data-testid={`${testIdPrefix}-rules-preset`}
			/>
			{note && (
				<p
					className="text-muted-foreground text-xs"
					data-testid={`${testIdPrefix}-coverage-note`}
				>
					{note}
				</p>
			)}
			{preset === 'custom' && (
				<RuleListEditor
					rules={customRules}
					onChange={onCustomRulesChange}
					pathSuggestions={pathSuggestions}
					opTemplates={pathSuggestions}
					opsLoaded={pathSuggestions.length > 0}
				/>
			)}
			{tester && (
				<section
					aria-labelledby={testerId}
					className="space-y-2 pt-2"
					data-testid={`${testIdPrefix}-tester`}
				>
					<h3 id={testerId} className="text-foreground text-sm font-medium">
						Try a request
					</h3>
					<DraftRuleTester
						rules={draftRules}
						describeMatch={describeMatch}
						paths={pathSuggestions}
						disabled={disabled}
					/>
				</section>
			)}
		</div>
	);
}
