import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { test, expect, type Page } from '@playwright/test';
import {
	authHeaders,
	bearer,
	bindCredentialToAgent,
	createApiKeyCredential,
	dismissFirstRunFor,
	importInlineApi,
	promoteLatestRevision,
	replaceAgentPermissions,
	uniqueSuffix,
} from './helpers';
import { provisionAdminOwnedAgent, type AgentIdentity } from './agent-flow';

/**
 * Both themes against the real backend: every route and the key sheets and
 * dialogs render in light and in dark, each view is screenshotted for review,
 * and axe's colour-contrast rule runs on it.
 *
 * The screenshots land in `test-results/themes/<theme>/<view>.png` and the axe
 * findings in `test-results/themes/contrast.json`. The spec fails on any
 * serious or critical contrast finding in either theme.
 */

const require = createRequire(import.meta.url);
const AXE_PATH = require.resolve('axe-core/axe.min.js');
const OUT_DIR = join(process.cwd(), 'test-results', 'themes');
const THEMES = ['light', 'dark'] as const;
type Theme = (typeof THEMES)[number];

interface Finding {
	theme: Theme;
	view: string;
	id: string;
	impact: string | null;
	nodes: { target: string; summary: string }[];
}

const findings: Finding[] = [];
/** How many text nodes axe judged, and how many it could not decide, per view. */
const coverage: { theme: Theme; view: string; checked: number; undecided: number }[] = [];

interface Seed {
	agent: AgentIdentity;
	connector: AgentIdentity;
	vendor: string;
	apiName: string;
	sessionId: string | null;
}

let seed: Seed;

/** An OpenAPI doc with an API-key scheme, so a connect session can target it. */
function apiKeySpec(title: string, host: string): string {
	return JSON.stringify({
		openapi: '3.0.0',
		info: { title, version: '1.0.0' },
		servers: [{ url: `https://${host}` }],
		components: {
			securitySchemes: { apiKey: { type: 'apiKey', in: 'header', name: 'X-Api-Key' } },
		},
		security: [{ apiKey: [] }],
		paths: {
			'/items': {
				get: {
					operationId: 'listItems',
					summary: 'List items',
					responses: { '200': { description: 'ok' } },
				},
			},
		},
	});
}

async function useTheme(page: Page, theme: Theme): Promise<void> {
	await page.addInitScript(([key, value]) => window.localStorage.setItem(key, value), [
		'jentic-one.theme',
		theme,
	] as const);
}

/** Screenshot the view and record its contrast findings. */
async function capture(page: Page, theme: Theme, view: string): Promise<void> {
	await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
	// Let entrance animations and first reads settle.
	await page.waitForTimeout(600);
	mkdirSync(join(OUT_DIR, theme), { recursive: true });
	// Docs runs to tens of thousands of pixels: its first screen is the review copy.
	await page.screenshot({
		path: join(OUT_DIR, theme, `${view}.png`),
		fullPage: view !== 'docs',
	});
	await page.addScriptTag({ path: AXE_PATH });
	const violations = await page.evaluate(async () => {
		const axe = (window as unknown as { axe: { run: (...a: unknown[]) => Promise<unknown> } })
			.axe;
		const result = (await axe.run(document, {
			runOnly: { type: 'rule', values: ['color-contrast', 'link-in-text-block'] },
		})) as {
			passes: { nodes: unknown[] }[];
			incomplete: { id: string; nodes: unknown[] }[];
			violations: {
				id: string;
				impact: string | null;
				nodes: { target: string[]; failureSummary?: string }[];
			}[];
		};
		const checked = result.passes.reduce((n, r) => n + r.nodes.length, 0);
		const undecided = result.incomplete.reduce((n, r) => n + r.nodes.length, 0);
		(window as unknown as { __axeCounts: number[] }).__axeCounts = [checked, undecided];
		return result.violations.map((v) => ({
			id: v.id,
			impact: v.impact,
			nodes: v.nodes.map((n) => ({
				target: n.target.join(' '),
				summary: (n.failureSummary ?? '').split('\n').slice(1).join(' ').trim(),
			})),
		}));
	});
	for (const v of violations) findings.push({ theme, view, ...v });
	const [checked, undecided] = await page.evaluate(
		() => (window as unknown as { __axeCounts: number[] }).__axeCounts,
	);
	coverage.push({ theme, view, checked, undecided });
}

test.beforeAll(async ({ request }) => {
	const sfx = uniqueSuffix();
	const vendor = `e2e-theme-${sfx}`;
	const apiName = `theme-${sfx}`;
	await importInlineApi(request, {
		vendor,
		apiName,
		content: apiKeySpec(`Theme check ${sfx}`, `${vendor}.example.com`),
	});
	await promoteLatestRevision(request, { vendor, name: apiName, version: '1.0.0' });

	const agent = await provisionAdminOwnedAgent(request, { name: `e2e-theme-agent-${sfx}` });
	const credentialId = await createApiKeyCredential(request, {
		name: `e2e-theme-key-${sfx}`,
		vendor,
		apiName,
		apiVersion: '1.0.0',
	});
	await bindCredentialToAgent(request, agent.clientId, credentialId);

	// A second agent with an open connect request, for "Waiting for you" and the
	// approval dialog.
	const connector = await provisionAdminOwnedAgent(request, { name: `e2e-theme-asks-${sfx}` });
	await replaceAgentPermissions(request, connector.clientId, ['credentials:connect']);
	const res = await request.post('/integrations:connect', {
		headers: bearer(connector.accessToken),
		data: { api: { vendor, name: apiName, version: '1.0.0' }, reason: 'theme check' },
	});
	const sessionId =
		res.status() === 201 ? ((await res.json()) as { session_id: string }).session_id : null;
	seed = { agent, connector, vendor, apiName, sessionId };
});

test.afterAll(async ({ request }) => {
	if (seed?.sessionId) {
		await request.post(`/connect-sessions/${seed.sessionId}:reject`, {
			headers: authHeaders(),
			data: { reason: 'theme check cleanup' },
		});
	}
	mkdirSync(OUT_DIR, { recursive: true });
	writeFileSync(join(OUT_DIR, 'contrast.json'), JSON.stringify({ findings, coverage }, null, 2));
});

for (const theme of THEMES) {
	test.describe(`${theme} theme`, () => {
		test.beforeEach(async ({ page }) => {
			await page.setViewportSize({ width: 1440, height: 900 });
			await useTheme(page, theme);
		});

		test('agents: fleet, selected agent, access sidebar, inventory, new agent', async ({
			page,
		}) => {
			await dismissFirstRunFor(page, seed.agent.clientId);
			await dismissFirstRunFor(page, seed.connector.clientId);
			await page.goto(`/app/agents?agent=${seed.agent.clientId}`);
			await expect(page.getByRole('heading', { level: 1, name: 'Agents' })).toBeVisible();
			await expect(page.getByTestId('api-tile').first()).toBeVisible();
			await capture(page, theme, 'agents');

			await page.getByRole('radio', { name: 'Cards view' }).click();
			await expect(page.getByTestId('api-card').first()).toBeVisible();
			await capture(page, theme, 'agents-cards');
			await page.getByRole('radio', { name: 'List view' }).click();

			await page
				.getByRole('button', { name: /open access details|^Manage access for / })
				.first()
				.click();
			await expect(page.getByTestId('sheet-primitive')).toBeVisible();
			await capture(page, theme, 'agents-access-sidebar');
			await page.keyboard.press('Escape');

			await page.goto(`/app/agents?credentials=1`);
			await expect(page.getByTestId('sheet-primitive')).toBeVisible();
			await capture(page, theme, 'agents-credential-inventory');
			await page.keyboard.press('Escape');

			await page.goto('/app/agents');
			await expect(page.getByRole('heading', { level: 1, name: 'Agents' })).toBeVisible();
			await page
				.getByRole('button', { name: /^New agent/ })
				.first()
				.click();
			await expect(page.getByRole('dialog').first()).toBeVisible();
			await capture(page, theme, 'agents-new-agent');
		});

		test('agents: waiting for you and the approval dialog', async ({ page }) => {
			test.skip(seed.sessionId == null, 'no API-target connect session on this deployment');
			await page.goto(`/app/agents?agent=${seed.connector.clientId}`);
			await expect(page.getByRole('region', { name: 'Waiting for you' })).toBeVisible();
			await capture(page, theme, 'agents-waiting-for-you');

			await page.goto(
				`/app/agents?agent=${seed.connector.clientId}&approve=${seed.sessionId}`,
			);
			await expect(page.getByRole('dialog', { name: /^Approve integration$/ })).toBeVisible();
			await capture(page, theme, 'agents-approve-dialog');
		});

		test('library, API hub, discover', async ({ page }) => {
			await page.goto('/app/library');
			await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
			await capture(page, theme, 'library');

			await page.goto(`/app/library/workspace/${seed.vendor}/${seed.apiName}/1.0.0`);
			await expect(page.getByTestId('hub-access')).toBeVisible();
			await capture(page, theme, 'library-api-hub');

			await page.goto('/app/discover');
			await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
			await capture(page, theme, 'discover');
		});

		test('monitor, access requests, settings', async ({ page }) => {
			for (const [route, view] of [
				['/app/monitor', 'monitor'],
				['/app/access-requests', 'access-requests'],
				['/app/settings', 'settings'],
				['/app/settings/developer', 'settings-developer'],
			] as const) {
				await page.goto(route);
				await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
				await capture(page, theme, view);
			}
		});

		test('notifications and the user menu', async ({ page }) => {
			await page.goto('/app/agents');
			await expect(page.getByRole('heading', { level: 1, name: 'Agents' })).toBeVisible();
			await page.getByRole('button', { name: /^Notifications/ }).click();
			await expect(page.getByRole('dialog', { name: /Notifications/ })).toBeVisible();
			await capture(page, theme, 'notifications');
			await page.keyboard.press('Escape');

			await page.getByRole('button', { name: /account menu|user menu/i }).click();
			await expect(page.getByRole('menu')).toBeVisible();
			await capture(page, theme, 'user-menu');
		});

		test('agents: every dock panel', async ({ page }) => {
			await page.goto(`/app/agents?agent=${seed.agent.clientId}`);
			await expect(page.getByTestId('api-tile').first()).toBeVisible();
			for (const panel of ['API key', 'Permissions', 'Activity', 'MCP', 'Settings']) {
				await page.getByRole('button', { name: panel, exact: true }).click();
				await expect(page.getByTestId('sheet-primitive')).toBeVisible();
				await capture(page, theme, `agents-dock-${panel.replace(' ', '-').toLowerCase()}`);
				await page.keyboard.press('Escape');
				await expect(page.getByTestId('sheet-primitive')).toHaveCount(0);
			}
		});

		test('agents: edit credential, the rule editor, archive confirm', async ({ page }) => {
			await page.goto(`/app/agents?agent=${seed.agent.clientId}`);
			await page
				.getByRole('button', { name: /^Manage access for / })
				.first()
				.click();
			await expect(page.getByTestId('sheet-primitive')).toBeVisible();
			await page.getByRole('button', { name: 'Add rule' }).first().click();
			await capture(page, theme, 'agents-rule-editor');
			await page.getByRole('button', { name: 'Edit credential' }).click();
			await expect(
				page.getByRole('heading', { name: /Edit credential/i }).first(),
			).toBeVisible();
			await capture(page, theme, 'agents-edit-credential');
			await page.goto(`/app/agents?agent=${seed.agent.clientId}`);
			await page.getByRole('button', { name: /^Archive / }).click();
			await expect(page.getByRole('dialog').first()).toBeVisible();
			await capture(page, theme, 'agents-archive-confirm');
		});

		test('agents: Add APIs tray and the setup queue', async ({ page }) => {
			await page.goto(`/app/agents?agent=${seed.connector.clientId}`);
			await page.getByTestId('card-add-api').click();
			const tray = page.getByRole('dialog', { name: 'Add APIs' });
			await expect(tray).toBeVisible();
			await capture(page, theme, 'agents-add-apis-tray');
			await tray
				.getByRole('checkbox', { name: new RegExp(`/${seed.apiName}@1\\.0\\.0`) })
				.click();
			await tray.getByRole('button', { name: 'Continue' }).click();
			await expect(page.getByRole('heading', { name: /^Set up 1 API$/ })).toBeVisible();
			await capture(page, theme, 'agents-setup-queue');
			const pick = page.getByRole('radio', { name: /e2e-theme-key-/ });
			if (await pick.count()) await pick.first().check();
			await page.getByRole('button', { name: 'Use this credential' }).click();
			await expect(page.getByTestId('queue-access-step')).toBeVisible();
			await capture(page, theme, 'agents-setup-queue-access');
		});

		test('library: API detail sheet and the import dialog', async ({ page }) => {
			await page.goto('/app/library');
			await page
				.getByRole('button', { name: /^Import your own API/ })
				.first()
				.click();
			await expect(page.getByRole('dialog').first()).toBeVisible();
			await capture(page, theme, 'library-import-dialog');
			await page.keyboard.press('Escape');
			await page.goto('/app/library');
			await page.getByText('abbyy.com', { exact: true }).first().click();
			await expect(page.getByTestId('sheet-primitive')).toBeVisible();
			await capture(page, theme, 'library-api-detail');
		});

		test('activity rail and settings tabs', async ({ page }) => {
			await page.goto('/app/agents');
			await page.getByRole('button', { name: /^Show live activity/ }).click();
			await expect(page.getByRole('complementary', { name: 'Activity' })).toBeVisible();
			await capture(page, theme, 'activity-rail');
			await page.goto('/app/settings');
			await page.getByRole('tab', { name: /Approval queue/ }).click();
			await capture(page, theme, 'settings-approval-queue');
			await page.getByRole('tab', { name: /Clients/ }).click();
			await page
				.getByRole('button', { name: /^Add client/ })
				.first()
				.click();
			await expect(page.getByRole('dialog').first()).toBeVisible();
			await capture(page, theme, 'settings-add-client');
		});

		test('docs', async ({ page }) => {
			await page.goto('/app/docs');
			await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();
			await capture(page, theme, 'docs');
		});

		test('sign-in, signed out', async ({ browser }) => {
			const context = await browser.newContext({
				storageState: { cookies: [], origins: [] },
			});
			const page = await context.newPage();
			await useTheme(page, theme);
			await page.goto('/app/login');
			await expect(page.getByRole('button', { name: /sign in/i })).toBeVisible();
			await capture(page, theme, 'login');
			await context.close();
		});
	});
}

test('no serious contrast findings in either theme', () => {
	const serious = findings.filter((f) => f.impact === 'serious' || f.impact === 'critical');
	expect(
		serious.map(
			(f) => `${f.theme} ${f.view}: ${f.id} on ${f.nodes.map((n) => n.target).join(', ')}`,
		),
	).toEqual([]);
});
