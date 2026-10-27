/**
 * The manual create-agent form: its state (`useAgentCreateForm`), its fields
 * and its actions, composed by the surface that hosts it. Fields reset only
 * after a successful create; a dismissal preserves the draft, so the host keeps
 * the hook above whatever unmounts on close.
 *
 * `Create` leaves the host surface open: an agent with nothing bound can
 * authenticate but every call it makes fails, so "created" is not a finished
 * state, and the host offers the agent's first API next.
 */
import { useEffect, useId, useRef, useState } from 'react';
import { Button, Input, Label, Textarea } from '@/shared/ui';
import { useCreateAgent, type AgentEntity } from '@/modules/agents/api';
import { DuplicateNameHint } from '@/modules/agents/components/DuplicateNameHint';
import { InitialPermissionsField } from '@/modules/agents/components/InitialPermissionsField';
import {
	AGENT_NAME_MAX_LENGTH,
	agentNameError,
	duplicateAgentName,
} from '@/modules/agents/lib/agentName';

export interface AgentCreateFormOptions {
	/** Whether the hosting surface is open — transient errors clear on each open. */
	open: boolean;
	/** The agent that was just created; the surface is still open. */
	onCreated?: (agent: AgentEntity) => void;
}

/** The draft starts empty: the register routes' command name names a different agent. */
export function useAgentCreateForm({ open, onCreated }: AgentCreateFormOptions) {
	const [name, setName] = useState('');
	const [description, setDescription] = useState('');
	const [permissions, setPermissions] = useState<string[]>([]);
	const [error, setError] = useState<string | null>(null);
	const nameRef = useRef<HTMLInputElement>(null);
	const create = useCreateAgent();

	useEffect(() => {
		if (open) setError(null);
	}, [open]);

	async function submit() {
		const trimmed = name.trim();
		const invalid = agentNameError(name);
		if (invalid) {
			setError(invalid);
			return;
		}
		try {
			const agent = await create.mutateAsync({
				name: trimmed,
				description: description.trim() || null,
				permissions,
			});
			setName('');
			setDescription('');
			setPermissions([]);
			setError(null);
			onCreated?.(agent);
		} catch {
			// hook surfaces a toast; keep the draft so the user can retry.
		}
	}

	return {
		name,
		setName,
		description,
		setDescription,
		permissions,
		setPermissions,
		error,
		pending: create.isPending,
		nameRef,
		submit,
	};
}

export type AgentCreateFormState = ReturnType<typeof useAgentCreateForm>;

/** The intro line and the form's fields. `existingNames` (the org's agents,
 * archived ones included) flags a name another agent already has. */
export function AgentCreateFields({
	form,
	existingNames = [],
}: {
	form: AgentCreateFormState;
	existingNames?: readonly string[];
}) {
	const hintId = useId();
	const duplicateOf = form.error ? null : duplicateAgentName(existingNames, form.name);
	return (
		<div className="space-y-4">
			<div className="space-y-1.5">
				<Label htmlFor="agent-name">Name</Label>
				<Input
					ref={form.nameRef}
					id="agent-name"
					value={form.name}
					onChange={(e) => form.setName(e.target.value)}
					placeholder="e.g. inbox-triage-bot"
					error={form.error ?? undefined}
					maxLength={AGENT_NAME_MAX_LENGTH}
					// Only while shown: an explicit `undefined` would drop the error's own link.
					{...(duplicateOf ? { 'aria-describedby': hintId } : {})}
				/>
				{duplicateOf && <DuplicateNameHint id={hintId} existing={duplicateOf} />}
			</div>
			<div className="space-y-1.5">
				<Label htmlFor="agent-description">Description</Label>
				<Textarea
					id="agent-description"
					value={form.description}
					onChange={(e) => form.setDescription(e.target.value)}
					placeholder="What does this agent do?"
					rows={3}
					maxLength={1024}
				/>
			</div>
			<InitialPermissionsField
				selected={form.permissions}
				onChange={form.setPermissions}
				idPrefix="agent-create"
			/>
		</div>
	);
}

/** Cancel, then Create — primary right-most. */
export function AgentCreateActions({
	form,
	onCancel,
}: {
	form: AgentCreateFormState;
	onCancel: () => void;
}) {
	return (
		<>
			{/* Phones: the header's close does it, so Create keeps the row. */}
			<Button
				variant="ghost"
				onClick={onCancel}
				disabled={form.pending}
				className="hidden sm:inline-flex"
			>
				Cancel
			</Button>
			<Button onClick={() => void form.submit()} loading={form.pending}>
				Create
			</Button>
		</>
	);
}
