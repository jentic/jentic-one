import { useEffect, useRef, useState } from 'react';
import { Dialog } from '@/shared/ui/Dialog';
import { Button } from '@/shared/ui/Button';
import { ErrorAlert } from '@/shared/ui/ErrorAlert';
import { Input } from '@/shared/ui/Input';
import { Label } from '@/shared/ui/Label';
import { Textarea } from '@/shared/ui/Textarea';
import { validateName, validateDescription } from '@/shared/lib';

/**
 * The subset of fields a rename/re-describe Save can patch. Only the keys that
 * actually changed relative to the seeded snapshot are present, so a rename-only
 * Save never rewrites an untouched description (and an empty patch is caught
 * before any request fires — see the Save gating below).
 */
export interface NameDescriptionPatch {
	name?: string;
	description?: string | null;
}

export interface EditNameDescriptionDialogProps {
	open: boolean;
	/**
	 * Dismiss handler. The dialog gates this internally while `isPending` so an
	 * in-flight Save can't be cut short by Escape / backdrop / Cancel (#1); the
	 * host still passes its own close (e.g. `() => setEditOpen(false)`).
	 */
	onClose: () => void;
	/** Sentence-case dialog title, e.g. `Edit agent` / `Edit toolkit` (#15). */
	title: string;
	/**
	 * Current entity name to seed the draft from. The dialog snapshots this the
	 * moment it opens (or the moment it first becomes available while open, for
	 * the open-before-load race) and diffs Save against that snapshot (#2, #3).
	 */
	initialName: string;
	/** Current entity description to seed the draft from (null → empty field). */
	initialDescription: string | null;
	/** True while the host mutation is in flight — gates dismissal + Save (#1). */
	isPending: boolean;
	/** Server error from the host mutation, surfaced inline (no toast). */
	error?: Error | null;
	/** Commit the diff-vs-seeded patch. Host owns the mutation + its onSuccess. */
	onSave: (patch: NameDescriptionPatch) => void;
	/** Save button label. Defaults to `Save changes`. */
	saveLabel?: string;
	/**
	 * True when the underlying entity has gone missing mid-edit (e.g. the query
	 * flipped to `undefined`). Save is disabled and a message is surfaced so the
	 * user isn't left clicking a silently no-op button (#5).
	 */
	entityMissing?: boolean;
	/** Id prefix for the name/description fields (keeps labels unique per host). */
	fieldIdPrefix?: string;
}

/**
 * Shared Edit (rename / re-describe) dialog for the agent + toolkit detail
 * pages. Encapsulates the Dialog + inline ErrorAlert + Name/Description fields +
 * Cancel/Save footer, plus the hardening the two pages used to duplicate (and
 * drift on):
 *
 *   - #1 Reopen-race: while the Save is in flight, neither Cancel, Escape, nor
 *     the backdrop can close the dialog (`dismissOnBackdrop={!isPending}` +
 *     an `onClose` guard), so a stale in-flight success can't slam a freshly
 *     reopened dialog shut.
 *   - #2 Seeded snapshot: the seeded name/description are captured into refs at
 *     open time and Save diffs the draft against THOSE, not the live (polled)
 *     entity — so a background refetch mid-edit can't skew the diff.
 *   - #5 Save is disabled (with a visible message) when the entity has gone
 *     missing mid-edit.
 *   - #8 Save is disabled when the computed patch is empty (no changed fields),
 *     so an unedited Save never round-trips a PATCH.
 *   - #10 The draft reseeds from the initial props each time the dialog opens
 *     and clears on close, so a cancelled draft never persists into the next
 *     session.
 *
 * Layering: this lives in `shared/ui` and must NOT import from `modules/*`. Each
 * host keeps ownership of its mutation, its title, and passing the current
 * name/description as the initial values.
 */
export function EditNameDescriptionDialog({
	open,
	onClose,
	title,
	initialName,
	initialDescription,
	isPending,
	error,
	onSave,
	saveLabel = 'Save changes',
	entityMissing = false,
	fieldIdPrefix = 'edit',
}: EditNameDescriptionDialogProps) {
	const [name, setName] = useState('');
	const [description, setDescription] = useState('');

	// The seeded snapshot Save diffs against (#2). Captured at seed time — not
	// read live from props — so a background poll that changes the entity while
	// the dialog is open can't skew the "what changed?" comparison.
	const seededNameRef = useRef('');
	const seededDescRef = useRef('');
	// Whether THIS open-session has been seeded yet. The host pencil can be
	// clicked before the entity query resolves, so we (re)seed when the dialog
	// is open and a real name is available but we haven't seeded this session
	// (#3), and reset the flag on close so the next open reseeds fresh (#10).
	const seededRef = useRef(false);

	useEffect(() => {
		if (!open) {
			// Clear on close so a cancelled draft never persists into a reopen (#10).
			seededRef.current = false;
			setName('');
			setDescription('');
			seededNameRef.current = '';
			seededDescRef.current = '';
			return;
		}
		// Seed once per open-session, as soon as a real name is available. If the
		// dialog opened before the entity loaded, `initialName` is empty here and
		// we defer until it arrives (this effect re-runs when the props change).
		if (seededRef.current) return;
		if (!initialName) return;
		// Seed from the TRIMMED name so what the user sees in the Input matches
		// the basis Save diffs against (#1). A padded seeded name would otherwise
		// always read as "changed" from its own trim, silently populating
		// `patch.name` on a description-only edit.
		const seededName = initialName.trim();
		const seededDesc = initialDescription ?? '';
		setName(seededName);
		setDescription(seededDesc);
		seededNameRef.current = seededName;
		seededDescRef.current = seededDesc;
		seededRef.current = true;
	}, [open, initialName, initialDescription]);

	const nameError = open ? validateName(name) : null;
	const descError = open ? validateDescription(description) : null;

	// Diff the draft against the SEEDED snapshot (#2), not the live props. Only
	// changed fields land in the patch; a rename-only Save omits the description
	// entirely so an untouched (even trailing-whitespace) description isn't
	// silently re-trimmed. Both name and description are normalized on BOTH
	// sides of the comparison so a no-op whitespace edit never fires a patch
	// (#1/#3): the seeded name is already trimmed, and the description compares
	// its normalized (`trim() || null`) forms.
	const trimmedName = name.trim();
	const normalizedDesc = description.trim() || null;
	const seededDesc = seededDescRef.current.trim() || null;
	const patch: NameDescriptionPatch = {};
	if (trimmedName !== seededNameRef.current) patch.name = trimmedName;
	if (normalizedDesc !== seededDesc) patch.description = normalizedDesc;
	const patchIsEmpty = Object.keys(patch).length === 0;

	const saveDisabled =
		nameError != null ||
		descError != null ||
		isPending ||
		entityMissing ||
		patchIsEmpty ||
		!seededRef.current;

	// Gate dismissal while a Save is in flight (#1): Escape + backdrop route
	// through Dialog.onClose, and Cancel calls this too — early-return so none
	// of them can close a dialog whose mutation hasn't settled.
	const handleClose = () => {
		if (isPending) return;
		onClose();
	};

	const handleSave = () => {
		if (saveDisabled) return;
		onSave(patch);
	};

	return (
		<Dialog
			open={open}
			onClose={handleClose}
			title={title}
			size="sm"
			dismissOnBackdrop={!isPending}
			footer={
				<>
					<Button variant="secondary" disabled={isPending} onClick={handleClose}>
						Cancel
					</Button>
					<Button disabled={saveDisabled} loading={isPending} onClick={handleSave}>
						{isPending ? 'Saving…' : saveLabel}
					</Button>
				</>
			}
		>
			<div className="space-y-4">
				{entityMissing && (
					<ErrorAlert message="This item is no longer available — reopen it to edit." />
				)}
				{error && <ErrorAlert message={error} />}
				<div>
					<Label
						htmlFor={`${fieldIdPrefix}-name`}
						className="text-muted-foreground mb-1 block text-xs"
					>
						Name
					</Label>
					<Input
						id={`${fieldIdPrefix}-name`}
						type="text"
						value={name}
						error={nameError ?? undefined}
						onChange={(e) => setName(e.target.value)}
					/>
				</div>
				<div>
					<Label
						htmlFor={`${fieldIdPrefix}-description`}
						className="text-muted-foreground mb-1 block text-xs"
					>
						Description
					</Label>
					<Textarea
						id={`${fieldIdPrefix}-description`}
						value={description}
						error={descError ?? undefined}
						onChange={(e) => setDescription(e.target.value)}
						rows={3}
					/>
				</div>
			</div>
		</Dialog>
	);
}
