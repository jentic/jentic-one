/**
 * AgentSettingsPanel — the "Settings" tab of the agent detail console, built
 * from the shared console cards so it reads identically to the other
 * detail consoles' Settings tabs:
 *   - {@link IdentitySettingsCard} → the immutable, copyable agent id plus
 *     editable name / description via PATCH /agents/{id}. Only dirty fields
 *     are sent (real PATCH semantics). Ownership is intentionally NOT
 *     editable here — reassigning an agent's accountable human is an
 *     administrative act, not routine metadata upkeep. For an ARCHIVED agent
 *     the card renders read-only (id row + note, no form): the backend
 *     rejects edits to an archived agent with 409, so an editable form would
 *     invite a write that can only fail.
 *   - {@link DangerZone} → the terminal Archive action. Suspension is NOT
 *     here: the reversible Disable/Enable flip lives in the page header's
 *     kill switch. The button defers to
 *     the page-level {@link LifecycleDialogs} via `onLifecycle` — this panel
 *     never mutates lifecycle state itself.
 *
 * Between the two sits `afterIdentity`, an optional slot for read-only facts
 * that belong with the identity but must stay above the destructive verb. The
 * flat surface's dock sheet fills it with {@link AgentProvenance}; the console
 * leaves it empty because its Overview tab already carries that block.
 */
import type { ReactNode } from 'react';
import { DangerZone, IdentitySettingsCard, type DangerZoneAction } from '@/shared/ui';
import {
	useUpdateAgent,
	ACTIONS_FOR_STATUS,
	type AgentEntity,
	type AgentPatch,
} from '@/modules/agents/api';

interface AgentSettingsPanelProps {
	agent: AgentEntity;
	/** Ask the page to run a destructive lifecycle action (opens its dialog). */
	onLifecycle: (action: 'archive') => void;
	/** True while any page-level lifecycle mutation is in flight. */
	lifecyclePending: boolean;
	/** Read-only facts rendered between the identity card and the danger zone. */
	afterIdentity?: ReactNode;
}

export function AgentSettingsPanel({
	agent,
	onLifecycle,
	lifecyclePending,
	afterIdentity,
}: AgentSettingsPanelProps) {
	const update = useUpdateAgent();

	// Archived is terminal and immutable — `AgentService.update_agent`
	// rejects it with 409 — so the identity card must not invite edits.
	const isArchived = agent.status === 'archived';

	const actions = ACTIONS_FOR_STATUS[agent.status];
	const dangerActions: DangerZoneAction[] = actions.includes('archive')
		? [
				{
					key: 'archive',
					title: 'Archive agent',
					description:
						'Removes this agent from the fleet and cascades to its bindings. This cannot be undone.',
					buttonLabel: 'Archive',
					ariaLabel: `Archive ${agent.name}`,
				},
			]
		: [];

	return (
		<div className="space-y-4">
			<IdentitySettingsCard
				idLabel="Agent ID"
				idValue={agent.id}
				name={agent.name}
				description={agent.description ?? null}
				saving={update.isPending}
				descriptionPlaceholder="What does this agent do?"
				readOnlyNote={
					isArchived
						? 'This agent is archived — its name and description are frozen.'
						: undefined
				}
				onSave={
					isArchived
						? undefined
						: async (draft) => {
								// PATCH semantics: only ship the fields that changed;
								// empty string on the nullable description means
								// "clear it".
								const patch: AgentPatch = {};
								if (draft.name !== agent.name) patch.name = draft.name;
								if (draft.description !== (agent.description ?? '')) {
									patch.description = draft.description || null;
								}
								// The hook toasts failures; a rejection keeps the
								// draft in the card so the user can retry.
								await update.mutateAsync({ id: agent.id, patch });
							}
				}
			/>

			{afterIdentity}

			<DangerZone
				actions={dangerActions}
				pending={lifecyclePending}
				onAction={() => onLifecycle('archive')}
			/>
		</div>
	);
}
