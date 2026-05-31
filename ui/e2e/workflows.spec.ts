import { test, expect } from '@playwright/test';
import {
	captureConsoleErrors,
	mockAuthenticatedUser,
	mockCatalog,
	mockCredentials,
	mockToolkits,
	mockTraces,
	mockWorkflowDetail,
	mockWorkflows,
	navigateTo,
} from './fixtures';

// The standalone `/workflows` list page was retired when the IA
// collapsed "what's mine" into a single Workspace surface. The list
// route now redirects to `/workspace`, while `/workflows/:slug` deep
// links rewrite to `/workspace/workflows/:slug`. These specs guard
// the redirect contract and verify the post-redirect Workspace +
// Workflow detail UI still renders.
test.describe('Legacy /workflows redirect', () => {
	test.beforeEach(async ({ page }) => {
		await mockAuthenticatedUser(page);
		await mockWorkflows(page);
		await mockCatalog(page);
		await mockCredentials(page);
		await mockToolkits(page);
		// Workspace stats strip pulls a "last trace" — stub it so the
		// post-redirect render doesn't surface unrelated 401s.
		await mockTraces(page);
	});

	test('redirects /workflows to /workspace and renders the workspace shell', async ({ page }) => {
		const errors = captureConsoleErrors(page);
		await page.goto('/');
		await navigateTo(page, '/workflows');
		await expect(page).toHaveURL(/\/workspace(\?|$)/);
		await expect(page.getByRole('heading', { name: 'Workspace', exact: true })).toBeVisible();
		expect(errors).toHaveLength(0);
	});

	test('preserves the query string when redirecting from /workflows', async ({ page }) => {
		await page.goto('/');
		await navigateTo(page, '/workflows?q=stripe');
		await expect(page).toHaveURL(/\/workspace\?[^#]*q=stripe/);
	});
});

test.describe('Legacy /workflows/:slug redirect', () => {
	test('redirects to /workspace/workflows/:slug and renders the detail view', async ({
		page,
	}) => {
		const errors = captureConsoleErrors(page);
		await mockAuthenticatedUser(page);
		await mockWorkflowDetail(page, 'test-workflow');
		await mockWorkflows(page);
		await mockCatalog(page);
		await mockCredentials(page);
		await mockToolkits(page);
		await mockTraces(page);

		await page.goto('/');
		await navigateTo(page, '/workflows/test-workflow');
		await expect(page).toHaveURL(/\/workspace\/workflows\/test-workflow(\?|$)/);
		await expect(page.getByRole('heading', { name: 'Test Workflow' })).toBeVisible();
		// The detail page exposes a shared back affordance (label was
		// shortened from the legacy "Back to workflows" copy). The
		// component renders as a <button> when history-aware (default),
		// so we target it via test-id.
		await expect(page.getByTestId('back-button').first()).toBeVisible();
		expect(errors).toHaveLength(0);
	});
});
