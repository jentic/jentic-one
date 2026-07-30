/**
 * AgentSettingsPanel — the "Settings" tab of the agent detail console.
 *
 * Two concerns, deliberately separated (canvas plan, phase 4):
 *   - Editable metadata → name / description / owner via PATCH /agents/{id}.
 *     Only dirty fields are sent (real PATCH semantics), and Save stays
 *     disabled until something actually changed.
 *   - Danger zone → the destructive lifecycle actions (Disable / Archive)
 *     move here from the identity header, so the header only ever offers
 *     constructive actions (Approve / Deny / Enable). Both destructive
 *     buttons defer to the page-level {@link LifecycleDialogs} via
 *     `onLifecycle` — this panel never mutates lifecycle state itself.
 */
import { useEffect, useRef, useState } from 'react';
import { Button, Card, CardBody, CardHeader, CardTitle, Input, Label, Textarea } from '@/shared/ui';
import {
	useUpdateAgent,
	ACTIONS_FOR_STATUS,
	type AgentEntity,
	type AgentPatch,
} from '@/modules/agents/api';
import { DangerZone, type DangerZoneItem } from '@/modules/agents/components/detail/shared';

interface AgentSettingsPanelProps {
	agent: AgentEntity;
	/** Ask the page to run a destructive lifecycle action (opens its dialog). */
	onLifecycle: (action: 'disable' | 'archive') => void;
	/** True while any page-level lifecycle mutation is in flight. */
	lifecyclePending: boolean;
}

export function AgentSettingsPanel({
	agent,
	onLifecycle,
	lifecyclePending,
}: AgentSettingsPanelProps) {
	const update = useUpdateAgent();

	const [name, setName] = useState(agent.name);
	const [description, setDescription] = useState(agent.description ?? '');
	const [ownerId, setOwnerId] = useState(agent.ownerId ?? '');
	const [nameError, setNameError] = useState<string | null>(null);

	// Seed-from-props syncs only when the entity itself changed — never on
	// re-renders of the same agent, or a background refetch would clobber the
	// user's draft. Without this, a warm detail cache could carry agent A's
	// draft onto agent B and PATCH the wrong row.
	const seededIdRef = useRef(agent.id);
	useEffect(() => {
		if (seededIdRef.current === agent.id) return;
		seededIdRef.current = agent.id;
		setName(agent.name);
		setDescription(agent.description ?? '');
		setOwnerId(agent.ownerId ?? '');
		setNameError(null);
	}, [agent.id, agent.name, agent.description, agent.ownerId]);

	const trimmedName = name.trim();
	const dirty =
		trimmedName !== agent.name ||
		description.trim() !== (agent.description ?? '') ||
		ownerId.trim() !== (agent.ownerId ?? '');

	async function handleSave() {
		if (!trimmedName) {
			setNameError('A name is required.');
			return;
		}
		setNameError(null);
		// PATCH semantics: only ship the fields that changed; empty string on a
		// nullable field means "clear it".
		const patch: AgentPatch = {};
		if (trimmedName !== agent.name) patch.name = trimmedName;
		if (description.trim() !== (agent.description ?? '')) {
			patch.description = description.trim() || null;
		}
		if (ownerId.trim() !== (agent.ownerId ?? '')) {
			patch.ownerId = ownerId.trim() || null;
		}
		try {
			const next = await update.mutateAsync({ id: agent.id, patch });
			// Re-seed the drafts from the server's canonical row so the form
			// goes back to a clean (non-dirty) state.
			setName(next.name);
			setDescription(next.description ?? '');
			setOwnerId(next.ownerId ?? '');
		} catch {
			// The hook toasts the failure; keep the draft so the user can retry.
		}
	}

	const actions = ACTIONS_FOR_STATUS[agent.status];
	const dangerItems: DangerZoneItem[] = [
		...(actions.includes('disable')
			? [
					{
						action: 'disable' as const,
						title: 'Disable agent',
						description:
							'Immediately revokes this agent’s ability to authenticate. Reversible — you can re-enable it later.',
						ariaLabel: `Disable ${agent.name}`,
					},
				]
			: []),
		...(actions.includes('archive')
			? [
					{
						action: 'archive' as const,
						title: 'Archive agent',
						description:
							'Removes this agent from the fleet and cascades to its bindings. This cannot be undone.',
						ariaLabel: `Archive ${agent.name}`,
					},
				]
			: []),
	];

	return (
		<div className="space-y-4">
			<Card>
				<CardHeader>
					<CardTitle>General</CardTitle>
				</CardHeader>
				<CardBody className="space-y-4">
					<div className="max-w-md space-y-1.5">
						<Label htmlFor="agent-settings-name">Name</Label>
						<Input
							id="agent-settings-name"
							value={name}
							onChange={(e) => setName(e.target.value)}
							error={nameError ?? undefined}
							maxLength={255}
						/>
					</div>
					<div className="max-w-md space-y-1.5">
						<Label htmlFor="agent-settings-description">Description</Label>
						<Textarea
							id="agent-settings-description"
							value={description}
							onChange={(e) => setDescription(e.target.value)}
							placeholder="What does this agent do?"
							rows={3}
							maxLength={1024}
						/>
					</div>
					<div className="max-w-md space-y-1.5">
						<Label htmlFor="agent-settings-owner">Owner</Label>
						<Input
							id="agent-settings-owner"
							value={ownerId}
							onChange={(e) => setOwnerId(e.target.value)}
							placeholder="usr_… (leave empty for no owner)"
							className="font-mono"
						/>
						<p className="text-muted-foreground text-xs">
							The user accountable for this agent. Clearing the field removes the
							owner.
						</p>
					</div>
					<div className="flex justify-end">
						<Button
							size="sm"
							onClick={handleSave}
							loading={update.isPending}
							disabled={!dirty || update.isPending}
						>
							Save changes
						</Button>
					</div>
				</CardBody>
			</Card>

			<DangerZone items={dangerItems} pending={lifecyclePending} onAction={onLifecycle} />
		</div>
	);
}
