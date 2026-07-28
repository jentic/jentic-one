import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen, userEvent, within } from '@/__tests__/test-utils';
import {
	EditNameDescriptionDialog,
	type NameDescriptionPatch,
} from '@/shared/ui/EditNameDescriptionDialog';

/**
 * Focused unit tests for the shared rename/re-describe dialog. The two detail
 * pages (agents + toolkits) delegate all of the hardening below to this one
 * component, so the correctness findings (#1/#2/#5/#8/#10) are pinned here once
 * rather than duplicated per page.
 */

/** A minimal open/close harness so tests can reopen the dialog. */
function Harness(props: {
	initialName?: string;
	initialDescription?: string | null;
	isPending?: boolean;
	entityMissing?: boolean;
	error?: Error | null;
	onSave: (patch: NameDescriptionPatch) => void;
}) {
	const [open, setOpen] = useState(true);
	return (
		<>
			<button onClick={() => setOpen(true)}>reopen</button>
			<EditNameDescriptionDialog
				open={open}
				onClose={() => setOpen(false)}
				title="Edit thing"
				initialName={props.initialName ?? 'Alpha'}
				initialDescription={props.initialDescription ?? 'First'}
				isPending={props.isPending ?? false}
				entityMissing={props.entityMissing}
				error={props.error ?? null}
				onSave={props.onSave}
			/>
		</>
	);
}

describe('EditNameDescriptionDialog', () => {
	it('disables Save for an unedited (empty-patch) draft (#8)', async () => {
		render(<Harness onSave={vi.fn()} />);
		const dialog = await screen.findByRole('dialog', { name: 'Edit thing' });
		expect(within(dialog).getByRole('button', { name: /save changes/i })).toBeDisabled();
	});

	it('only sends the changed field in the patch, diffed against the seed (#2)', async () => {
		const onSave = vi.fn();
		const user = userEvent.setup();
		render(<Harness onSave={onSave} />);
		const dialog = await screen.findByRole('dialog', { name: 'Edit thing' });

		const nameInput = within(dialog).getByLabelText('Name');
		await user.clear(nameInput);
		await user.type(nameInput, 'Beta');
		await user.click(within(dialog).getByRole('button', { name: /save changes/i }));

		// Rename-only edit → the patch carries just `name`, not the untouched
		// description (so an unchanged description isn't silently rewritten).
		expect(onSave).toHaveBeenCalledTimes(1);
		expect(onSave).toHaveBeenCalledWith({ name: 'Beta' });
	});

	it('disables Save and surfaces a message when the entity goes missing mid-edit (#5)', async () => {
		render(<Harness entityMissing onSave={vi.fn()} />);
		const dialog = await screen.findByRole('dialog', { name: 'Edit thing' });

		expect(within(dialog).getByRole('button', { name: /save changes/i })).toBeDisabled();
		expect(within(dialog).getByText(/no longer available/i)).toBeInTheDocument();
	});

	it('reseeds the draft from the initial props on reopen, discarding a cancelled edit (#10)', async () => {
		const user = userEvent.setup();
		render(<Harness onSave={vi.fn()} />);
		let dialog = await screen.findByRole('dialog', { name: 'Edit thing' });

		// Edit then cancel — the draft must not persist into the next session.
		const nameInput = within(dialog).getByLabelText('Name');
		await user.clear(nameInput);
		await user.type(nameInput, 'Scratch');
		await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

		await user.click(screen.getByRole('button', { name: 'reopen' }));
		dialog = await screen.findByRole('dialog', { name: 'Edit thing' });
		expect(within(dialog).getByLabelText('Name')).toHaveValue('Alpha');
		expect(within(dialog).getByLabelText('Description')).toHaveValue('First');
	});

	it('does not close on Cancel while a save is pending (#1)', async () => {
		const user = userEvent.setup();
		render(<Harness isPending onSave={vi.fn()} />);
		const dialog = await screen.findByRole('dialog', { name: 'Edit thing' });

		const cancel = within(dialog).getByRole('button', { name: 'Cancel' });
		expect(cancel).toBeDisabled();
		await user.keyboard('{Escape}');
		// The pending guard early-returns, so Escape can't dismiss it either.
		expect(screen.getByRole('dialog', { name: 'Edit thing' })).toBeInTheDocument();
	});

	it('renders a server error inline without a toast', async () => {
		render(<Harness error={new Error('Name already in use.')} onSave={vi.fn()} />);
		const dialog = await screen.findByRole('dialog', { name: 'Edit thing' });
		expect(within(dialog).getByText(/name already in use/i)).toBeInTheDocument();
	});

	it('does not populate `name` for a description-only edit when the seeded name is padded (#1)', async () => {
		// The server can hand back a padded name (`'  My Toolkit  '`). The seed
		// is trimmed so the visible Input matches the diff basis; a
		// description-only edit must therefore omit `name` entirely rather than
		// silently renaming the entity to its own trim.
		const onSave = vi.fn();
		const user = userEvent.setup();
		render(
			<Harness initialName="  My Toolkit  " initialDescription="Old desc" onSave={onSave} />,
		);
		const dialog = await screen.findByRole('dialog', { name: 'Edit thing' });
		expect(within(dialog).getByLabelText('Name')).toHaveValue('My Toolkit');

		const descInput = within(dialog).getByLabelText('Description');
		await user.clear(descInput);
		await user.type(descInput, 'New desc');
		await user.click(within(dialog).getByRole('button', { name: /save changes/i }));

		expect(onSave).toHaveBeenCalledTimes(1);
		const patch = onSave.mock.calls[0][0];
		expect(patch).not.toHaveProperty('name');
		expect(patch).toEqual({ description: 'New desc' });
	});

	it('treats a trailing-space edit on an unchanged description as a no-op (empty patch, Save disabled) (#3)', async () => {
		// Trimming happens on BOTH sides of the diff, so typing a trailing space
		// on an otherwise-unchanged description fires no PATCH — the patch stays
		// empty and Save is disabled.
		const user = userEvent.setup();
		render(<Harness initialName="Alpha" initialDescription="foo" onSave={vi.fn()} />);
		const dialog = await screen.findByRole('dialog', { name: 'Edit thing' });

		const descInput = within(dialog).getByLabelText('Description');
		await user.clear(descInput);
		await user.type(descInput, 'foo ');
		expect(within(dialog).getByRole('button', { name: /save changes/i })).toBeDisabled();
	});

	it('treats a whitespace-only draft against a null seed as a no-op (no description in patch) (#3)', async () => {
		// A whitespace-only draft normalizes to `null`, which equals the seeded
		// null description — so it must not add `description: null` to the patch
		// (which the empty-patch guard would otherwise let through as a no-op).
		const user = userEvent.setup();
		render(<Harness initialName="Alpha" initialDescription={null} onSave={vi.fn()} />);
		const dialog = await screen.findByRole('dialog', { name: 'Edit thing' });

		const descInput = within(dialog).getByLabelText('Description');
		await user.type(descInput, '   ');
		// Description-only whitespace change → empty patch → Save disabled.
		expect(within(dialog).getByRole('button', { name: /save changes/i })).toBeDisabled();
	});

	it('seeds the fields once the real name arrives after opening before load (#12)', async () => {
		// The pencil can be clicked before the entity query resolves: `initialName`
		// is empty, so the seededRef effect defers. Once the real name arrives the
		// fields seed from it (this pins the `!initialName` guard + seededRef).
		const { rerender } = render(
			<Harness initialName="" initialDescription="Draft desc" onSave={vi.fn()} />,
		);
		const dialog = await screen.findByRole('dialog', { name: 'Edit thing' });
		// Nothing seeded yet — the name field is empty and Save is disabled.
		expect(within(dialog).getByLabelText('Name')).toHaveValue('');
		expect(within(dialog).getByRole('button', { name: /save changes/i })).toBeDisabled();

		rerender(
			<Harness initialName="Real Name" initialDescription="Draft desc" onSave={vi.fn()} />,
		);
		expect(within(dialog).getByLabelText('Name')).toHaveValue('Real Name');
		expect(within(dialog).getByLabelText('Description')).toHaveValue('Draft desc');
	});

	it('sends a description-only patch (no `name` key) when only the description is edited (#12)', async () => {
		const onSave = vi.fn();
		const user = userEvent.setup();
		render(<Harness initialName="Alpha" initialDescription="First" onSave={onSave} />);
		const dialog = await screen.findByRole('dialog', { name: 'Edit thing' });

		const descInput = within(dialog).getByLabelText('Description');
		await user.clear(descInput);
		await user.type(descInput, 'Second');
		await user.click(within(dialog).getByRole('button', { name: /save changes/i }));

		expect(onSave).toHaveBeenCalledTimes(1);
		const patch = onSave.mock.calls[0][0];
		expect(patch).toHaveProperty('description', 'Second');
		expect(patch).not.toHaveProperty('name');
	});
});
