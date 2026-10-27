/**
 * Manage access → Access, edit view: the heading, the draft's summary and bar,
 * then the rules container — its header with a quiet Close editor, the setup
 * queue's access step (presets as radio rows; under Custom rules, By action
 * with staged Allow · Ask · Deny choices and Apply, or By path as one ordered
 * list), and at its bottom, only while something is pending, the save area:
 * what a save would change, Discard changes, Save rules. A successful save
 * closes the editor (the host shows the read view with a transient "Saved").
 * The host owns the draft, the save and the close guard; Effective access
 * previews the DRAFT.
 */
import { useId, type ReactNode } from 'react';
import {
	ArrowUpDown,
	ListChecks,
	Minus,
	Plus,
	RotateCcw,
	Save,
	X,
	type LucideIcon,
} from 'lucide-react';
import { Button } from '@/shared/ui';
import {
	AccessRulesStep,
	type SiblingApiReference,
	type UnappliedChoicesHost,
} from '@/shared/credentials/components';
import { splitCustomRules, type ScopeReach } from '@/shared/credentials/lib/accessPresets';
import type { ActionCatalog } from '@/shared/credentials/lib/actionRules';
import type { DraftChanges } from '@/modules/agents/lib/accessDraft';
import { plural } from '@/modules/agents/lib/stripSummary';
import {
	AccessHeading,
	AccessSummary,
	EffectiveAccess,
} from '@/modules/agents/components/flat/access/AccessReadView';
import type { AccessDraft } from '@/modules/agents/components/flat/access/useAccessDraft';

const ACCESS_WORD = { allow: 'Allow', ask: 'Ask', deny: 'Deny', unset: 'Not set' } as const;

export interface AccessEditorProps {
	credentialName: string;
	reach: ScopeReach;
	apiReference: { vendor: string; name: string; version: string } | null;
	siblings: readonly SiblingApiReference[];
	onOpenSibling: (key: string) => void;
	systemRuleCount: number;
	catalog: ActionCatalog;
	draft: AccessDraft;
	/** By action's staged choices, which the host settles. */
	unapplied: UnappliedChoicesHost;
	onSave: () => void;
	/** Back to the read view; the host guards it while anything is pending. */
	onClose: () => void;
}

export function AccessEditor({
	credentialName,
	reach,
	apiReference,
	siblings,
	onOpenSibling,
	systemRuleCount,
	catalog,
	draft,
	unapplied,
	onSave,
	onClose,
}: AccessEditorProps) {
	const { toSave: draftRules, changes, dirty, invalid, saving } = draft;
	const { staged } = unapplied;
	const pending = dirty || staged > 0;
	const stagedReasonId = useId();
	return (
		<div className="space-y-3" data-testid="access-editor">
			<AccessHeading credentialName={credentialName} />
			<AccessSummary
				rules={draftRules}
				catalog={catalog}
				apiReference={apiReference}
				source="draft"
			/>
			<div
				className="border-hairline space-y-3 rounded-lg border p-3"
				data-testid="access-rules-container"
			>
				<div className="flex items-center gap-2">
					<p className="text-foreground-sub text-xs font-medium">Editing the rules</p>
					<Button
						variant="ghost"
						size="xs"
						className="text-foreground-sub hover:text-foreground ml-auto gap-1"
						onClick={onClose}
						disabled={saving}
					>
						<X className="h-3.5 w-3.5" aria-hidden="true" /> Close editor
					</Button>
				</div>
				<AccessRulesStep
					reach={reach}
					preset={draft.preset}
					onPresetChange={draft.setPreset}
					customRules={draft.customRules}
					onCustomRulesChange={draft.setCustomRules}
					apiReference={apiReference}
					// The section heading above names it; the radio group keeps it as its label.
					heading={<span className="sr-only">Permission rules for {credentialName}</span>}
					siblings={siblings}
					onOpenSibling={onOpenSibling}
					// By action unless path rules are already in play.
					initialBuild={
						splitCustomRules(draft.customRules).pathRules.length > 0 ? 'path' : 'action'
					}
					// Full access's caption already says how far the credential reaches.
					hideCoverageNote
					// Effective access sits below the editor, always visible.
					effectiveTool={false}
					disabled={saving}
					stagedRef={unapplied.stagedRef}
					onStagedChange={unapplied.onStagedChange}
					testIdPrefix="manage"
				/>
				{/* The save area, directly under the rules being edited (never
				    below Effective access, which can grow tall): only while
				    something is pending — what a save changes (removals first,
				    the security-critical signal), then Discard changes and Save rules. */}
				{pending && (
					<div
						className="bg-surface-inset space-y-3 rounded-md p-3"
						data-testid="access-commit"
					>
						{dirty && <PendingChanges changes={changes} />}
						<div className="flex flex-wrap items-center justify-between gap-2">
							<div className="min-w-0 text-xs" data-testid="rules-dirty-hint">
								<span className="text-foreground-lighter inline-flex items-center gap-1.5 font-medium">
									<span
										aria-hidden="true"
										className="bg-caution h-1.5 w-1.5 shrink-0 rounded-full"
									/>
									{dirty
										? plural(changes.count, 'unsaved change')
										: 'Nothing to save yet'}
								</span>
								{staged > 0 && (
									<span
										className="text-foreground-sub block"
										id={stagedReasonId}
										data-testid="rules-staged-reason"
									>
										Apply or reset the {plural(staged, 'choice')} by action
										before saving.
									</span>
								)}
								{invalid && (
									<span className="text-caution block">
										An Allow or Ask rule needs a method, path or action.
									</span>
								)}
							</div>
							<div className="flex flex-wrap items-center gap-2">
								<Button
									variant="secondary"
									size="sm"
									onClick={(): void => {
										unapplied.stagedRef.current?.discard();
										draft.discard();
									}}
									disabled={saving}
								>
									<RotateCcw className="h-4 w-4" /> Discard changes
								</Button>
								<Button
									size="sm"
									onClick={onSave}
									loading={saving}
									disabled={invalid || staged > 0 || !dirty}
									aria-describedby={staged > 0 ? stagedReasonId : undefined}
								>
									<Save className="h-4 w-4" /> {saving ? 'Saving…' : 'Save rules'}
								</Button>
							</div>
						</div>
					</div>
				)}
			</div>
			<EffectiveAccess
				apiReference={apiReference}
				rules={draftRules}
				source="draft"
				systemRuleCount={systemRuleCount}
			/>
		</div>
	);
}

/** What a save would change, in readable words. */
function PendingChanges({ changes }: { changes: DraftChanges }) {
	return (
		<div data-testid="rules-diff">
			<p className="text-foreground-sub mb-2 font-mono text-[10px] tracking-wide uppercase">
				Pending changes
				<span className="normal-case"> · applied when you save</span>
			</p>
			<ul className="space-y-1 text-xs">
				{changes.movedAhead > 0 && (
					<ChangeItem icon={ArrowUpDown} data-testid="rules-diff-moved-ahead">
						Moves {plural(changes.movedAhead, 'action choice')} ahead of the path rules,
						so a choice on a specific action always wins.
					</ChangeItem>
				)}
				{changes.reordered && (
					<ChangeItem icon={ArrowUpDown}>
						Rules reordered — first match wins, so the new order changes which rule
						decides a request.
					</ChangeItem>
				)}
				{changes.removed.map((rule, i) => (
					<ChangeItem key={`r${i}`} icon={Minus} className="text-danger">
						<span className="sr-only">Removed: </span>
						{rule}
					</ChangeItem>
				))}
				{changes.added.map((rule, i) => (
					<ChangeItem key={`a${i}`} icon={Plus} className="text-success">
						<span className="sr-only">Added: </span>
						{rule}
					</ChangeItem>
				))}
				{changes.actions.map((g) => (
					<ChangeItem key={g.to} icon={ListChecks}>
						<span className="font-medium">{ACCESS_WORD[g.to]}:</span>{' '}
						{g.names.join(', ')}
					</ChangeItem>
				))}
			</ul>
		</div>
	);
}

function ChangeItem({
	icon: Icon,
	className = 'text-foreground',
	children,
	'data-testid': testId,
}: {
	icon: LucideIcon;
	className?: string;
	children: ReactNode;
	'data-testid'?: string;
}) {
	return (
		<li className={`${className} flex items-start gap-1.5`} data-testid={testId}>
			<Icon className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
			<span>{children}</span>
		</li>
	);
}
