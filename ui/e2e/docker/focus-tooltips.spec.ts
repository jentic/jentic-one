import { test, expect, type Page } from '@playwright/test';
import {
	bindCredentialToAgent,
	createApiKeyCredential,
	dismissFirstRunFor,
	importInlineApi,
	replaceBindingRules,
	uniqueSuffix,
} from './helpers';
import { provisionAdminOwnedAgent, type AgentIdentity } from './agent-flow';

/**
 * A sheet that opens from a pointer click moves focus into itself (its first
 * control) and hands it back to the opener when it closes. That programmatic
 * focus must not open a tooltip or a hover card: they belong to hover and to
 * KEYBOARD focus only. A keyboard user tabbing onto the same control still
 * gets the tooltip.
 */

const ALLOW_ALL = [{ effect: 'allow', match_mode: 'regex', path: '.*' }];

let agent: AgentIdentity;

test.beforeAll(async ({ request }) => {
	const sfx = uniqueSuffix();
	const vendor = `e2e-focus-${sfx}`;
	const apiName = `focus-${sfx}`;
	await importInlineApi(request, { vendor, apiName });
	agent = await provisionAdminOwnedAgent(request, { name: `e2e-focus-${sfx}` });
	const credentialId = await createApiKeyCredential(request, {
		name: `e2e-focus-key-${sfx}`,
		vendor,
		apiName,
		apiVersion: '1.0.0',
	});
	await bindCredentialToAgent(request, agent.clientId, credentialId);
	await replaceBindingRules(request, credentialId, agent.clientId, ALLOW_ALL);
});

async function openAgent(page: Page): Promise<void> {
	await page.setViewportSize({ width: 1440, height: 900 });
	await dismissFirstRunFor(page, agent.clientId);
	await page.goto(`/app/agents?agent=${agent.clientId}`);
	await expect(page.getByTestId('api-tile').first()).toBeVisible();
}

test('clicking Manage access opens the sheet with no tooltip showing', async ({ page }) => {
	await openAgent(page);
	await page
		.getByRole('button', { name: /^Manage access for / })
		.first()
		.click();
	const sheet = page.getByTestId('sheet-primitive');
	await expect(sheet).toBeVisible();
	// Past every tooltip delay, so a focus-opened bubble would be up by now.
	await page.waitForTimeout(800);
	await expect(page.getByRole('tooltip')).toHaveCount(0);
});

test('Add APIs → the setup queue, by pointer, leaves no hover card over the sheet', async ({
	page,
	request,
}) => {
	const sfx = uniqueSuffix();
	const apiName = `queue-${sfx}`;
	await importInlineApi(request, { vendor: `e2e-queue-${sfx}`, apiName });
	await openAgent(page);
	await page.getByTestId('card-add-api').click();
	const tray = page.getByRole('dialog', { name: 'Add APIs' });
	await expect(tray).toBeVisible();
	await page.waitForTimeout(800);
	await expect(page.getByRole('tooltip')).toHaveCount(0);

	await tray.getByRole('checkbox', { name: new RegExp(`/${apiName}@1\\.0\\.0`) }).click();
	await tray.getByRole('button', { name: 'Continue' }).click();
	await expect(page.getByRole('heading', { name: /^Set up 1 API$/ })).toBeVisible();
	await page.waitForTimeout(800);
	await expect(page.getByRole('tooltip')).toHaveCount(0);
});

test('a keyboard user tabbing onto Pause still gets its tooltip', async ({ page }) => {
	await openAgent(page);
	await page
		.getByRole('button', { name: /^Manage access for / })
		.first()
		.click();
	await expect(page.getByTestId('sheet-primitive')).toBeVisible();
	const pause = page.getByRole('button', { name: /^Suspend binding for / });
	await expect(pause).toBeVisible();
	// Walk focus with the keyboard until it lands on Pause.
	for (let i = 0; i < 12; i++) {
		if (await pause.evaluate((el) => el === document.activeElement)) break;
		await page.keyboard.press('Tab');
	}
	await expect(pause).toBeFocused();
	await expect(page.getByRole('tooltip')).toBeVisible();
});
