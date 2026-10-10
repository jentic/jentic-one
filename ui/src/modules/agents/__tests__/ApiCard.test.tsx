/**
 * ApiCard — one API the agent can call, as a dense identity tile in the cards
 * grid. Shows real tile fields (avatar, name, credential, status) with no
 * sparkline or counts; the whole card opens the SAME access sheet.
 */
import { describe, it, expect, vi } from 'vitest';
import {
	renderWithProviders,
	screen,
	fireEvent,
	checkA11y,
	userEvent,
} from '@/__tests__/test-utils';
import { ApiCard } from '@/modules/agents/components/flat/ApiCard';
import { accountLabels, type ApiTileModel } from '@/modules/agents/lib/apiTiles';
import { deriveTileStatus } from '@/modules/agents/lib/tileStatus';
import type { BindingRulesState } from '@/modules/agents/api';

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
		credentialCreatedAt: null,
		credentialUpdatedAt: null,
		boundAt: new Date().toISOString(),
		suspended: false,
		suspendedReason: null,
		awaitingConsent: false,
		updateAvailable: false,
		...over,
	};
}

function renderCard(
	tile: ApiTileModel,
	extra: {
		rules?: BindingRulesState;
		agentServing?: boolean;
		accountLabel?: string;
		accountCount?: number;
		expanded?: boolean;
		onOpen?: () => void;
	} = {},
) {
	const {
		rules = { total: 2, allow: 2, deny: 0 },
		agentServing = true,
		expanded = false,
		onOpen = () => {},
		...rest
	} = extra;
	return renderWithProviders(
		<ul style={{ width: 900 }}>
			<li>
				<ApiCard
					tile={tile}
					rules={rules}
					agentServing={agentServing}
					expanded={expanded}
					sidebarId="api-access-sidebar"
					onOpen={onOpen}
					{...rest}
				/>
			</li>
		</ul>,
	);
}

const card = () => screen.getByTestId('api-card');

describe('ApiCard', () => {
	it('renders the real tile fields: avatar, name, credential and a status word', async () => {
		const { container } = renderCard(makeTile());

		expect(screen.getByTestId('vendor-mark')).toBeInTheDocument();
		expect(card()).toHaveTextContent('Slack');
		expect(screen.getByTestId('card-credential')).toHaveTextContent('Slack bot token');
		expect(screen.getByTestId('card-status')).toHaveTextContent('Ready');
		// Dense card: no sparkline, no call counts.
		expect(screen.queryByText(/calls/)).toBeNull();
		expect(screen.queryByText(/7d/)).toBeNull();
		await checkA11y(container);
	});

	it('derives its status with the same deriveTileStatus the rows use', () => {
		for (const rules of [
			{ total: 2, allow: 2, deny: 0 },
			{ total: 0, allow: 0, deny: 0 },
			{ total: 2, allow: 0, deny: 2 },
		] as const) {
			const { unmount } = renderCard(makeTile(), { rules });
			const expected = deriveTileStatus({
				suspended: false,
				agentServing: true,
				awaitingConsent: false,
				rules,
			});
			expect(card()).toHaveAttribute('data-status', expected);
			unmount();
		}
	});

	it('shows the Blocked caution word when the binding has no rules', () => {
		renderCard(makeTile(), { rules: { total: 0, allow: 0, deny: 0 } });
		expect(card()).toHaveAttribute('data-status', 'blocked-no-rules');
		expect(screen.getByTestId('card-status')).toHaveTextContent('Blocked');
	});

	it('reads a suspended binding as Paused and dims the card', () => {
		renderCard(makeTile({ suspended: true }));
		expect(card()).toHaveAttribute('data-suspended', 'true');
		expect(screen.getByTestId('card-status')).toHaveTextContent('Paused');
	});

	it('shows Sign-in needed when the credential awaits consent', () => {
		renderCard(makeTile({ awaitingConsent: true }), { rules: undefined });
		expect(card()).toHaveAttribute('data-status', 'sign-in-needed');
	});

	it('opens the sheet on click', () => {
		const onOpen = vi.fn();
		renderCard(makeTile(), { onOpen });
		fireEvent.click(card());
		expect(onOpen).toHaveBeenCalledOnce();
	});

	it('opens the sheet on Enter and Space (the native button)', async () => {
		const user = userEvent.setup();
		const onOpen = vi.fn();
		renderCard(makeTile(), { onOpen });
		card().focus();
		await user.keyboard('{Enter}');
		await user.keyboard(' ');
		expect(onOpen).toHaveBeenCalledTimes(2);
	});

	it('names the full status in its tooltip and accessible name, not the one word', async () => {
		const user = userEvent.setup();
		const { unmount } = renderCard(makeTile(), { rules: { total: 0, allow: 0, deny: 0 } });
		expect(screen.getByTestId('card-status')).toHaveTextContent(/^Blocked$/);
		expect(card()).toHaveAccessibleName(/— Blocked · no rules\. Open access details\.$/);
		await user.hover(screen.getByTestId('card-status'));
		expect(await screen.findByRole('tooltip')).toHaveTextContent(/^Blocked · no rules$/);
		unmount();

		renderCard(makeTile({ suspended: true }), { rules: { total: 3, allow: 2, deny: 1 } });
		expect(screen.getByTestId('card-status')).toHaveTextContent(/^Paused$/);
		expect(card()).toHaveAccessibleName(/— Suspended · not serving\. Open access details\.$/);
		await user.hover(screen.getByTestId('card-status'));
		expect(await screen.findByRole('tooltip')).toHaveTextContent(
			'Suspended · not serving · 3 access rules · 1 deny',
		);
	});

	it('is a focusable button that controls the sidebar when expanded', () => {
		renderCard(makeTile(), { expanded: true });
		expect(card().tagName).toBe('BUTTON');
		expect(card()).toHaveAttribute('aria-haspopup', 'dialog');
		expect(card()).toHaveAttribute('aria-expanded', 'true');
		expect(card()).toHaveAttribute('aria-controls', 'api-access-sidebar');
	});

	it('shows a compact key+count affordance for a multi-credential API', () => {
		const tiles = [
			makeTile({ credentialId: 'cred_1' }),
			makeTile({
				key: 'acb_2:slack.com/default',
				bindingId: 'acb_2',
				credentialId: 'cred_abcdef123456',
			}),
		];
		renderCard(tiles[1], {
			accountLabel: accountLabels(tiles).get(tiles[1].key),
			accountCount: 2,
		});
		expect(screen.getByTestId('card-accounts-badge')).toHaveTextContent('2');
		// The credential line names this account (its id tail disambiguates).
		expect(screen.getByTestId('card-credential')).toHaveTextContent('Slack bot token');
	});

	it('falls back to "No credential" when the row carries no credential name', () => {
		renderCard(makeTile({ credentialName: '  ' }));
		expect(screen.getByTestId('card-credential')).toHaveTextContent('No credential');
	});

	it('is one fixed height, with the name and credential each held to one line', () => {
		const long = 'canada-holidays.ca (no auth) — a very long credential name that would wrap';
		const { unmount } = renderCard(makeTile({ credentialName: long }));
		const tall = card().getBoundingClientRect().height;
		const cred = screen.getByTestId('card-credential').getBoundingClientRect().height;
		unmount();
		renderCard(makeTile({ credentialName: 'k' }));
		// Same 92px box whatever the text, and a 16px (single) credential line.
		expect(tall).toBe(92);
		expect(card().getBoundingClientRect().height).toBe(92);
		expect(cred).toBe(16);
		expect(card().className).not.toMatch(/min-h-/);
	});

	it('puts the status row at the same offset whatever the content', () => {
		const offset = () =>
			Math.round(
				screen.getByTestId('card-status').getBoundingClientRect().top -
					card().getBoundingClientRect().top,
			);
		const { unmount } = renderCard(makeTile({ title: 'A', credentialName: 'b' }));
		const short = offset();
		unmount();
		renderCard(
			makeTile({
				title: 'An API with a very long title indeed',
				credentialName: 'x'.repeat(90),
			}),
			{ accountCount: 4, accountLabel: 'canada-holidays.ca · …4239' },
		);
		expect(offset()).toBe(short);
	});

	it('names the credential count in the accessible name for a multi-credential API', () => {
		renderCard(makeTile(), { accountCount: 3 });
		expect(card()).toHaveAccessibleName(/one of 3 credentials for this API/);
	});
});
