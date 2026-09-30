/**
 * ApiTile's footer: the credential (key icon, muted "Credential" label, name) and
 * the rules summary share one line; hovering or focusing the credential lists
 * the details the tile already holds.
 */
import { describe, it, expect } from 'vitest';
import { renderWithProviders, screen, within, checkA11y } from '@/__tests__/test-utils';
import { ApiTile } from '@/modules/agents/components/flat/ApiTile';
import { accountLabels, type ApiTileModel } from '@/modules/agents/lib/apiTiles';

function makeTile(over: Partial<ApiTileModel> = {}): ApiTileModel {
	return {
		key: 'acb_1:slack.com/default',
		title: 'Slack',
		host: 'slack.com',
		iconUrl: null,
		vendor: 'slack.com',
		apiName: 'default',
		version: '1.0.0',
		authLabel: 'Bearer token',
		operationCount: 9,
		bindingId: 'acb_1',
		credentialId: 'cred_0123456789abcdef',
		credentialName: 'Slack bot token',
		credentialCreatedAt: new Date(Date.now() - 3 * 86_400_000).toISOString(),
		credentialUpdatedAt: null,
		boundAt: new Date(Date.now() - 2 * 3_600_000).toISOString(),
		suspended: false,
		awaitingConsent: false,
		...over,
	};
}

function renderTile(
	tile: ApiTileModel,
	extra: { accountLabel?: string; accountCount?: number; width?: number } = {},
) {
	const { width, ...props } = extra;
	return renderWithProviders(
		<div style={{ width: width ?? 360 }}>
			<ApiTile
				tile={tile}
				rules={{ total: 0, allow: 0, deny: 0 }}
				onOpen={() => {}}
				onSuspend={() => {}}
				onResume={() => {}}
				bindingPending={false}
				agentServing
				expanded={false}
				sidebarId="api-access-sidebar"
				{...props}
			/>
		</div>,
	);
}

/** The credential's focusable tooltip trigger. */
function credentialTrigger(): HTMLElement {
	return screen.getByTestId('tile-credential').parentElement as HTMLElement;
}

describe('ApiTile credential footer', () => {
	it('shows the labelled credential and the rules summary on one line', async () => {
		const { container } = renderTile(makeTile());

		const slot = screen.getByTestId('tile-detail-slot');
		expect(screen.getByTestId('tile-credential-label')).toHaveTextContent(/^Slack bot token$/);
		expect(screen.getByTestId('tile-rules-summary')).toHaveTextContent(
			/^No rules — all calls blocked$/,
		);
		expect(slot).toContainElement(screen.getByTestId('tile-credential'));
		expect(slot).toContainElement(screen.getByTestId('tile-rules-summary'));
		// "Credential" is visible; its colon is for screen readers only.
		const credential = screen.getByTestId('tile-credential');
		expect(credential.querySelector('.sr-only')).toHaveTextContent(':');
		expect(within(credential).getByText('Credential')).toHaveClass('text-muted-foreground');
		// The header is untouched: no credential and no chip for one credential.
		expect(screen.getByRole('heading', { name: 'Slack' }).parentElement).not.toContainElement(
			credential,
		);
		expect(screen.queryByTestId('tile-accounts-badge')).toBeNull();
		await checkA11y(container);
	});

	it('lists the credential details on focus, as the trigger’s description', async () => {
		renderTile(makeTile());

		const trigger = credentialTrigger();
		trigger.focus();
		const tip = await screen.findByRole('tooltip');
		expect(trigger).toHaveAttribute('aria-describedby', tip.id);
		const details = within(tip).getByTestId('tile-credential-details');
		expect(details.textContent?.startsWith('Name: Slack bot token')).toBe(true);
		expect(details).toHaveTextContent('Auth: Bearer token');
		expect(details).toHaveTextContent('ID: …89abcdef');
		expect(details).toHaveTextContent('Scope: slack.com / default / v1.0.0');
		expect(details).toHaveTextContent('Added: 3d ago');
		expect(details).toHaveTextContent('Bound: 2h ago');
	});

	it('leaves out details the tile does not hold', async () => {
		renderTile(
			makeTile({
				authLabel: null,
				credentialCreatedAt: null,
				apiName: null,
				version: null,
			}),
		);

		credentialTrigger().focus();
		const details = within(await screen.findByRole('tooltip')).getByTestId(
			'tile-credential-details',
		);
		expect(details).not.toHaveTextContent('Auth:');
		expect(details).not.toHaveTextContent('Added:');
		expect(details).not.toHaveTextContent('—');
		expect(details).toHaveTextContent('Scope: slack.com / all APIs');
	});

	it('truncates the credential name before the rules summary', () => {
		renderTile(
			makeTile({ credentialName: 'Slack workspace bot token for the weekend support rota' }),
			{ width: 280 },
		);

		const name = screen.getByTestId('tile-credential-label');
		const rulesText = screen.getByTestId('tile-rules-summary');
		expect(name.scrollWidth).toBeGreaterThan(name.clientWidth);
		expect(rulesText.scrollWidth).toBeLessThanOrEqual(rulesText.clientWidth);
		expect(screen.getByTestId('tile-detail-slot').getBoundingClientRect().height).toBe(18);
	});

	it('names each of two same-named credentials with its id tail', () => {
		const tiles = [
			makeTile({ credentialId: 'cred_1' }),
			makeTile({
				key: 'acb_2:slack.com/default',
				bindingId: 'acb_2',
				credentialId: 'cred_abcdef123456',
			}),
		];
		renderTile(tiles[1], {
			accountLabel: accountLabels(tiles).get(tiles[1].key),
			accountCount: 2,
		});

		expect(screen.getByTestId('tile-credential-label')).toHaveTextContent(
			'Slack bot token · …123456',
		);
		// The header chip is unchanged.
		expect(screen.getByTestId('tile-accounts-badge')).toHaveTextContent('2 credentials');
	});

	it('says "No credential" when the tile has no credential name', () => {
		renderTile(makeTile({ credentialName: '  ' }));

		const credential = screen.getByTestId('tile-credential');
		expect(credential).toHaveTextContent(/^No credential$/);
		expect(screen.queryByTestId('tile-credential-label')).toBeNull();
		expect(screen.getByTestId('tile-rules-summary')).toBeInTheDocument();
	});
});
