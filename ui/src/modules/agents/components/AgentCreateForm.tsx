/**
 * The manual create-agent form: its state (`useAgentCreateForm`), its fields
 * and its actions, composed by the surface that hosts it. Fields reset only
 * after a successful create; a dismissal preserves the draft, so the host keeps
 * the hook above whatever unmounts on close.
 *
 * Creating flows straight into the Add-APIs step: an agent with nothing bound can
 * authenticate but every call it makes fails, so "created" is not a finished
 * state. `Create empty` stays available, de-emphasised, for reserving an identity.
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
	onClose: () => void;
	/**
	 * The agent that was just created, and whether the operator asked to carry
	 * on into the Add-APIs flow for it.
	 */
	onCreated?: (agent: AgentEntity, opts: { addApis: boolean }) => void;
	/** A name to start the draft from — the one typed in the landing's register
	 * card, so either path in creates the agent the operator named. */
	initialName?: string;
}

/** Which button is in flight, so only that one spins. */
type Intent = 'add-apis' | 'empty';

export function useAgentCreateForm({
	open,
	onClose,
	onCreated,
	initialName = '',
}: AgentCreateFormOptions) {
	const [name, setName] = useState(initialName);
	const [description, setDescription] = useState('');
	const [permissions, setPermissions] = useState<string[]>([]);
	const [error, setError] = useState<string | null>(null);
	const [intent, setIntent] = useState<Intent | null>(null);
	const nameRef = useRef<HTMLInputElement>(null);
	const create = useCreateAgent();

	useEffect(() => {
		if (open) setError(null);
	}, [open]);

	// The seed syncs only when it changes, and only over a draft that still
	// reads as the previous seed: a name edited here is the operator's and stays.
	const lastSeedRef = useRef(initialName);
	useEffect(() => {
		const previous = lastSeedRef.current;
		if (previous === initialName) return;
		lastSeedRef.current = initialName;
		setName((current) => (current === previous ? initialName : current));
	}, [initialName]);

	async function submit(next: Intent) {
		const trimmed = name.trim();
		const invalid = agentNameError(name);
		if (invalid) {
			setError(invalid);
			return;
		}
		setIntent(next);
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
			onClose();
			// After the close, so the host's tray opens onto a dismissed surface
			// rather than stacking a second layer over this one.
			onCreated?.(agent, { addApis: next === 'add-apis' });
		} catch {
			// hook surfaces a toast; keep the draft so the user can retry.
		} finally {
			setIntent(null);
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
		intent,
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
			<p className="text-muted-foreground text-sm">
				Agents represent autonomous actors on the platform. New agents are created as active
				and can authenticate immediately — you pick the APIs they can reach next.
			</p>
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

/** Cancel, then the two creates — primary right-most. */
export function AgentCreateActions({
	form,
	onCancel,
}: {
	form: AgentCreateFormState;
	onCancel: () => void;
}) {
	const { pending, intent } = form;
	return (
		<>
			<Button variant="ghost" onClick={onCancel} disabled={pending}>
				Cancel
			</Button>
			{/* De-emphasised, not hidden: reserving an identity ahead of the
			    credentials it will need is a real case, and the operator who
			    takes this exit lands on the agent's own screen, where the
			    same Add-APIs step is one click away. */}
			<Button
				variant="secondary"
				onClick={() => void form.submit('empty')}
				loading={pending && intent === 'empty'}
				disabled={pending && intent !== 'empty'}
			>
				Create empty
			</Button>
			<Button
				onClick={() => void form.submit('add-apis')}
				loading={pending && intent === 'add-apis'}
				disabled={pending && intent !== 'add-apis'}
			>
				Create and add APIs
			</Button>
		</>
	);
}
