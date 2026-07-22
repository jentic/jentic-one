import { test, expect } from '@playwright/test';
import { createToolkit, uniqueSuffix } from './helpers';

/**
 * Cascade delete (real backend).
 *
 * Hard-deleting a toolkit is irreversible and cascades server-side to keys,
 * bindings, and permission rules, so the UI gates it behind the shared
 * `CascadeDeleteDialog` (type-to-confirm). This drives that flow for real
 * (DELETE /toolkits/{id} -> 204) from the toolkit detail page.
 *
 * NOTE on the grouped blast-radius list: the dialog renders a per-dependent
 * breakdown only when a caller passes `dependents`, which today requires a
 * backend "dependents"/dry-run endpoint that does not exist yet (see
 * CascadeDeleteDialog.tsx). Until it lands, the real flow shows the
 * type-specific GENERIC warning — which is what we assert here. The grouped
 * blast-radius assertion is captured as a fixme so it's ready when the endpoint
 * arrives.
 */

test('hard-delete a toolkit from the detail page (type-to-confirm, generic warning)', async ({
	page,
	request,
}) => {
	const name = `e2e-cascade-tk-${uniqueSuffix()}`;
	const toolkitId = await createToolkit(request, name);

	await page.goto(`/app/toolkits/${toolkitId}`);
	await expect(page.getByRole('heading', { name })).toBeVisible();

	// Open the cascade dialog from the danger-zone delete control.
	await page
		.getByRole('button', { name: `Delete ${name}` })
		.first()
		.click();

	const dialog = page.getByRole('dialog', { name: 'Delete toolkit' });
	await expect(dialog).toBeVisible();
	// Generic warning copy: "<name> will be permanently …". The grouped
	// blast-radius list isn't shown (no dependents endpoint yet — see fixme).
	await expect(dialog).toContainText(/will be permanently/i);

	// Confirm is gated behind typing the fixed confirm word "delete".
	const confirm = dialog.getByRole('button', { name: 'Delete toolkit', exact: true });
	await expect(confirm).toBeDisabled();
	await dialog.getByRole('textbox', { name: /Type delete to confirm/i }).fill('delete');
	await expect(confirm).toBeEnabled();
	await confirm.click();

	// Real DELETE /toolkits/{id} -> 204; the UI navigates away from the detail
	// page and the toolkit is gone from the list.
	await expect(dialog).toBeHidden();
	await page.goto('/app/toolkits');
	await expect(page.getByRole('heading', { name: 'Toolkits' })).toBeVisible();
	await expect(page.getByText(name)).toBeHidden();
});

// Grouped blast-radius (counts + names per dependent group). Blocked on a
// backend dependents/dry-run endpoint that the dialog can call before deleting.
test.fixme('cascade dialog shows the grouped blast-radius list when a toolkit has dependents', async () => {
	// Seed a toolkit with a key + an agent binding, open the delete dialog,
	// and assert "will also remove N dependents" + the per-group counts.
	// Requires the dependents endpoint (see CascadeDeleteDialog.tsx).
});
