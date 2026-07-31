import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Fingerprint, SlidersHorizontal } from 'lucide-react';
import { Button } from '@/shared/ui/Button';
import { CopyButton } from '@/shared/ui/CopyButton';
import { DetailSection } from '@/shared/ui/DetailSection';
import { ErrorAlert } from '@/shared/ui/ErrorAlert';
import { Input } from '@/shared/ui/Input';
import { Label } from '@/shared/ui/Label';
import { Textarea } from '@/shared/ui/Textarea';

/**
 * IdentitySettingsCard — the Settings tab's "General" card shared by the
 * detail consoles (toolkit, agent, service account): the immutable, copyable
 * entity id plus the editable name/description form.
 *
 * One grammar everywhere:
 *   - the id row leads (what API calls and audit rows reference — it lives
 *     here, not in the page chrome, so the header stays clean);
 *   - name is required, description optional;
 *   - "Save changes" sits bottom-RIGHT (form-primary convention) and stays
 *     disabled until something actually changed;
 *   - drafts seed from props only when the entity id changes (dialog-state
 *     rule applied to an inline form): a background refetch must never
 *     clobber an in-progress draft, but navigating to a sibling entity must
 *     reseed.
 *
 * Consoles without an update endpoint (service accounts today) omit `onSave`
 * and pass `readOnlyNote` — the card renders the id row plus the explanation
 * instead of a dead form.
 */

export interface IdentitySettingsCardProps {
	/** Label for the immutable id row ("Agent ID", "Toolkit ID", "Account ID"). */
	idLabel: string;
	idValue: string;
	/** The entity's current (saved) name. */
	name: string;
	/** The entity's current (saved) description. */
	description: string | null;
	/**
	 * Persist a draft. Callers diff against their entity for PATCH semantics
	 * and MUST throw/reject on failure so the draft is kept for a retry.
	 * `description` is the trimmed draft — empty string means "clear it".
	 */
	onSave?: (draft: { name: string; description: string }) => Promise<void>;
	saving?: boolean;
	/** Mutation error surfaced inline above the actions row. */
	error?: string | null;
	/** Read-only explanation when there is no update endpoint (no `onSave`). */
	readOnlyNote?: ReactNode;
	descriptionPlaceholder?: string;
}

export function IdentitySettingsCard({
	idLabel,
	idValue,
	name,
	description,
	onSave,
	saving = false,
	error,
	readOnlyNote,
	descriptionPlaceholder,
}: IdentitySettingsCardProps) {
	const [draftName, setDraftName] = useState(name);
	const [draftDescription, setDraftDescription] = useState(description ?? '');
	const [nameError, setNameError] = useState<string | null>(null);
	// What the server last acknowledged — dirtiness compares against THIS, not
	// live props, so the form reads clean the instant a save resolves (before
	// any cache refetch) and a background refetch can't flip a clean form dirty.
	const [saved, setSaved] = useState({ name, description: description ?? '' });

	// Seed-from-props syncs only when the entity itself changed — never on
	// re-renders of the same entity, or a background refetch would clobber
	// the user's draft (and a warm cache could carry entity A's draft onto
	// entity B and PATCH the wrong row).
	const seededIdRef = useRef(idValue);
	useEffect(() => {
		if (seededIdRef.current === idValue) return;
		seededIdRef.current = idValue;
		setDraftName(name);
		setDraftDescription(description ?? '');
		setSaved({ name, description: description ?? '' });
		setNameError(null);
	}, [idValue, name, description]);

	const trimmedName = draftName.trim();
	const trimmedDescription = draftDescription.trim();
	const dirty = trimmedName !== saved.name || trimmedDescription !== saved.description;

	async function handleSave() {
		if (!onSave) return;
		if (!trimmedName) {
			setNameError('A name is required.');
			return;
		}
		setNameError(null);
		try {
			await onSave({ name: trimmedName, description: trimmedDescription });
			// Re-seed from what was submitted so the form returns to a clean
			// (non-dirty) state even before the cache refetch lands.
			setDraftName(trimmedName);
			setDraftDescription(trimmedDescription);
			setSaved({ name: trimmedName, description: trimmedDescription });
		} catch {
			// The caller surfaces the failure (toast / `error` prop); keep the
			// draft so the user can retry.
		}
	}

	return (
		<DetailSection title="General" icon={<SlidersHorizontal className="h-4 w-4" />}>
			{/* The immutable entity id — what API calls and audit rows reference. */}
			<div className="flex flex-wrap items-center justify-between gap-2">
				<span className="text-muted-foreground flex items-center gap-1.5 text-xs">
					<Fingerprint className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
					{idLabel}
				</span>
				<span className="bg-muted text-muted-foreground inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 font-mono text-xs">
					{idValue}
					<CopyButton value={idValue} size="icon" variant="ghost" />
				</span>
			</div>

			{onSave ? (
				<form
					className="space-y-4 pt-2"
					onSubmit={(e) => {
						e.preventDefault();
						void handleSave();
					}}
				>
					<div className="space-y-1.5">
						<Label htmlFor="identity-settings-name">Name</Label>
						<Input
							id="identity-settings-name"
							value={draftName}
							onChange={(e) => setDraftName(e.target.value)}
							error={nameError ?? undefined}
							maxLength={255}
						/>
					</div>
					<div className="space-y-1.5">
						<Label htmlFor="identity-settings-description">Description</Label>
						<Textarea
							id="identity-settings-description"
							value={draftDescription}
							onChange={(e) => setDraftDescription(e.target.value)}
							placeholder={descriptionPlaceholder}
							rows={3}
							maxLength={1024}
						/>
					</div>
					{error && <ErrorAlert message={error} />}
					<div className="flex justify-end">
						<Button
							type="submit"
							size="sm"
							loading={saving}
							disabled={!dirty || saving}
						>
							Save changes
						</Button>
					</div>
				</form>
			) : (
				readOnlyNote && <p className="text-muted-foreground pt-1 text-sm">{readOnlyNote}</p>
			)}
		</DetailSection>
	);
}
