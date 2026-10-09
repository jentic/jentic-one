/**
 * ApiRow — one "Can call" row: clean at rest, previewed on hover intent or
 * keyboard focus, pinned open (any number at once) by a click, tap, Enter/Space
 * or its chevron; its Manage access opens the access sheet.
 */
import { describe, it, expect, vi } from 'vitest';
import {
	act,
	renderWithProviders,
	screen,
	within,
	checkA11y,
	fireEvent,
	settleAnimations,
	waitFor,
	userEvent,
} from '@/__tests__/test-utils';
import { ApiRow } from '@/modules/agents/components/flat/ApiRow';
import { HOVER_INTENT, usePinStack } from '@/shared/hooks';
import { REVEAL_MOTION } from '@/shared/ui';
import { accountLabels, type ApiTileModel } from '@/modules/agents/lib/apiTiles';
import type { ApiRowActivity } from '@/modules/agents/lib/apiRowActivity';
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
		credentialCreatedAt: new Date(Date.now() - 3 * 86_400_000).toISOString(),
		credentialUpdatedAt: null,
		boundAt: new Date(Date.now() - 2 * 3_600_000).toISOString(),
		suspended: false,
		suspendedReason: null,
		awaitingConsent: false,
		updateAvailable: false,
		...over,
	};
}

const QUIET: ApiRowActivity = {
	usage: { total: 0, success: 0, failed: 0, avgMs: 0, trend: [] },
	percentiles: null,
	calls: [],
	scanned: { count: 0, hasMore: false },
};

const BUSY: ApiRowActivity = {
	usage: { total: 42, success: 40, failed: 2, avgMs: 180, trend: [1, 4, 2, 8, 5, 9, 13] },
	percentiles: { p50Ms: 150, p95Ms: 610 },
	calls: [
		{
			id: 'exe_1',
			toolkitId: null,
			credentialId: 'cred_0123456789abcdef',
			operationId: null,
			operationMethod: 'post',
			operationPath: '/chat.postMessage',
			status: 'succeeded',
			durationMs: 212,
			httpStatus: 200,
			error: null,
			startedAt: new Date(Date.now() - 4 * 60_000).toISOString(),
			api: { vendor: 'slack.com', name: 'default' },
			traceId: 'trace_aaaaaaaa',
		},
	] as ApiRowActivity['calls'],
	scanned: { count: 1, hasMore: false },
};

const SLACK = makeTile();
const GITHUB = makeTile({
	key: 'acb_2:github',
	title: 'GitHub',
	host: 'api.github.com',
	bindingId: 'acb_2',
	credentialName: 'GitHub app',
});
const STRIPE = makeTile({
	key: 'acb_3:stripe',
	title: 'Stripe',
	host: 'api.stripe.com',
	bindingId: 'acb_3',
	credentialName: 'Stripe live',
});

interface RowsProps {
	tiles: ApiTileModel[];
	width?: number;
	rules?: BindingRulesState;
	activity?: ApiRowActivity;
	accountLabel?: string;
	accountCount?: number;
	onOpen?: (title: string) => void;
	onOpenRules?: (title: string) => void;
}

/** Rows the way the agent section holds them: one pin stack for the list
 * (any number pinned at once), a row's pin dropped when its sheet opens. */
function Rows({
	tiles,
	width = 1100,
	rules = { total: 2, allow: 2, deny: 0 },
	activity = QUIET,
	onOpen = () => {},
	onOpenRules,
	...props
}: RowsProps) {
	const pins = usePinStack();
	return (
		<ul style={{ width }}>
			{tiles.map((tile) => (
				<li key={tile.key}>
					<ApiRow
						agentId="agt_1"
						tile={tile}
						rules={rules}
						activity={activity}
						onOpen={() => {
							pins.unpin(tile.key);
							onOpen(tile.title);
						}}
						onOpenRules={
							onOpenRules &&
							(() => {
								pins.unpin(tile.key);
								onOpenRules(tile.title);
							})
						}
						onSuspend={() => {}}
						onResume={() => {}}
						bindingPending={false}
						agentServing
						expanded={false}
						sidebarId="api-access-sidebar"
						pinned={pins.isPinned(tile.key)}
						onTogglePin={() => pins.toggle(tile.key)}
						{...props}
					/>
				</li>
			))}
		</ul>
	);
}

function renderRow(tile: ApiTileModel, extra: Omit<RowsProps, 'tiles'> = {}) {
	return renderWithProviders(<Rows tiles={[tile]} {...extra} />);
}

function renderRows(extra: Partial<RowsProps> = {}) {
	return renderWithProviders(<Rows tiles={[SLACK, GITHUB, STRIPE]} {...extra} />);
}

const row = () => screen.getByTestId('api-tile');
const rowOf = (title: string) =>
	screen.getByRole('heading', { name: title }).closest('[data-testid="api-tile"]') as HTMLElement;
/** The row's stretched header: the pointer's target (out of the tab order and
 * the accessibility tree — the chevron is the keyboard control). */
const header = (title: string) => within(rowOf(title)).getByTestId('row-header');
const chevron = (title: string) => within(rowOf(title)).getByTestId('row-toggle');
const manageButton = (title: string) => within(rowOf(title)).getByTestId('row-manage-access');
const revealOf = (title: string) => within(rowOf(title)).queryByTestId('api-row-reveal');

/**
 * Run a test on a fake clock — timers and `performance` (hover intent reads
 * both) — from before its render, real timers restored after. `advance` steps
 * it inside `act`. The reveal folds on its own fallback timer under reduced
 * motion, so a fold is `advance(FOLD_MS)`.
 */
async function onFakeClock(body: (advance: (ms: number) => void) => Promise<void> | void) {
	vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] });
	try {
		await body((ms) => act(() => void vi.advanceTimersByTime(ms)));
	} finally {
		vi.useRealTimers();
	}
}
/** Long enough for a reveal to fold (its fallback settles past `closeMs`). */
const FOLD_MS = REVEAL_MOTION.closeMs + 120;

/** A finger on the row's header: a touch press, then the click it makes. */
function tapRow(title: string) {
	fireEvent.pointerDown(header(title), { pointerType: 'touch' });
	fireEvent.click(header(title));
}
/** A mouse click on the row's header. */
function clickRow(title: string) {
	fireEvent.pointerDown(header(title), { pointerType: 'mouse' });
	fireEvent.click(header(title));
}

describe('ApiRow at rest', () => {
	it('shows identity, the labelled credential, one metric and one marker', async () => {
		const { container } = renderRow(makeTile());

		expect(screen.getByRole('heading', { name: 'Slack' })).toBeInTheDocument();
		expect(screen.getByTestId('tile-capability')).toHaveTextContent(
			'Bearer token · 9 operations',
		);
		expect(screen.getByTestId('tile-credential-label')).toHaveTextContent(/^Slack bot token$/);
		expect(screen.getByTestId('tile-rules-summary')).toHaveTextContent(/^2 access rules$/);
		expect(screen.getByTestId('row-metric')).toHaveTextContent('No calls yet');
		expect(screen.getByTestId('tile-status-chip')).toHaveTextContent('Ready');
		// "Credential" is visible; its colon is for screen readers only.
		expect(screen.getByTestId('tile-credential').querySelector('.sr-only')).toHaveTextContent(
			':',
		);
		expect(screen.queryByTestId('api-row-reveal')).toBeNull();
		expect(screen.queryByTestId('tile-accounts-badge')).toBeNull();
		await checkA11y(container);
	});

	it('prints the 7-day call count when the row carries traffic', () => {
		renderRow(makeTile(), { activity: BUSY });
		expect(screen.getByTestId('row-metric')).toHaveTextContent('42 calls · 7d');
	});

	it('marks an API with an upstream update', () => {
		renderRow(makeTile({ updateAvailable: true }));
		expect(screen.getByTestId('tile-update-available')).toHaveTextContent('Update available');
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
		renderRow(tiles[1], {
			accountLabel: accountLabels(tiles).get(tiles[1].key),
			accountCount: 2,
		});

		expect(screen.getByTestId('tile-credential-label')).toHaveTextContent(
			'Slack bot token · …3456',
		);
		expect(screen.getByTestId('tile-accounts-badge')).toHaveTextContent('2 credentials');
	});

	it('says "No credential" when the row has no credential name', () => {
		renderRow(makeTile({ credentialName: '  ' }));
		expect(screen.getByTestId('tile-credential')).toHaveTextContent(/^No credential$/);
	});

	it('reads a blocked row as a button to the rules, without a summary', () => {
		const onOpenRules = vi.fn();
		renderRow(makeTile(), { rules: { total: 0, allow: 0, deny: 0 }, onOpenRules });

		expect(screen.queryByTestId('tile-rules-summary')).toBeNull();
		expect(screen.getByTestId('row-metric')).toHaveTextContent('No calls · blocked');
		fireEvent.click(screen.getByTestId('tile-status-blocked'));
		expect(onOpenRules).toHaveBeenCalledOnce();
	});

	it('keeps a suspended row neutral, its marker naming the pause', () => {
		renderRow(makeTile({ suspended: true, suspendedReason: 'api_deleted' }));

		const marker = screen.getByTestId('tile-status-chip');
		expect(marker).toHaveTextContent('Suspended · not serving');
		expect(marker).toHaveAttribute('data-tone', 'muted');
		expect(row()).toHaveAttribute('data-suspended', 'true');
	});

	it('suspended because its API was deleted, it keeps the credential label', () => {
		renderRow(makeTile({ suspended: true, suspendedReason: 'api_deleted' }));
		expect(screen.getByTestId('tile-credential')).toHaveTextContent(
			'Credential: Slack bot token',
		);
		expect(screen.getByTestId('tile-credential-label')).toHaveTextContent(/^Slack bot token$/);
		expect(screen.getByTestId('row-resume')).toHaveAccessibleName('Resume Slack access');
	});
});

describe('ApiRow reveal', () => {
	it('grows once the pointer rests on it, and folds after the leave grace', async () => {
		vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
		try {
			renderRow(makeTile(), { activity: BUSY });

			fireEvent.pointerEnter(row(), { pointerType: 'mouse' });
			fireEvent.pointerMove(row(), { pointerType: 'mouse', movementX: 2 });
			vi.advanceTimersByTime(HOVER_INTENT.openDelayMs - 30);
			expect(screen.queryByTestId('api-row-reveal')).toBeNull();
			vi.advanceTimersByTime(40);
		} finally {
			vi.useRealTimers();
		}
		expect(await screen.findByTestId('api-row-reveal')).toBeInTheDocument();
		expect(row()).toHaveAttribute('data-revealed', 'true');

		fireEvent.pointerLeave(row(), { pointerType: 'mouse' });
		await waitFor(() => expect(screen.queryByTestId('api-row-reveal')).toBeNull());
	});

	it('a pointer passing over does not grow the row', async () => {
		vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
		try {
			renderRow(makeTile());
			fireEvent.pointerEnter(row(), { pointerType: 'mouse' });
			fireEvent.pointerMove(row(), { pointerType: 'mouse', movementX: 2 });
			vi.advanceTimersByTime(60);
			fireEvent.pointerLeave(row(), { pointerType: 'mouse' });
			vi.advanceTimersByTime(HOVER_INTENT.openDelayMs * 2);
		} finally {
			vi.useRealTimers();
		}
		expect(screen.queryByTestId('api-row-reveal')).toBeNull();
	});

	it('grows, pinned, with the activity, access and recent calls', async () => {
		const { container } = renderRow(makeTile(), { activity: BUSY });

		fireEvent.click(chevron('Slack'));
		const reveal = await screen.findByTestId('api-row-reveal');

		expect(within(reveal).getByTestId('row-usage-line')).toHaveTextContent(
			'42 calls · 95.2% success · avg 180 ms · p95 610 ms',
		);
		const call = within(reveal).getByTestId('row-recent-call');
		expect(call).toHaveTextContent('/chat.postMessage');
		expect(call).toHaveTextContent('200');
		expect(call).toHaveTextContent('212 ms');
		expect(within(call).getByRole('link', { name: /Open trace/ })).toHaveAttribute(
			'href',
			'/monitor?show=calls&actor_id=agt_1&trace_id=trace_aaaaaaaa',
		);
		// The credential is the resting row's; the reveal doesn't repeat it.
		expect(within(row()).getByTestId('tile-credential-label')).toHaveTextContent(
			'Slack bot token',
		);
		expect(reveal).not.toHaveTextContent('Slack bot token');
		expect(within(reveal).getByRole('button', { name: 'Manage access' })).toBeInTheDocument();
		expect(within(reveal).getByRole('button', { name: /Pause/ })).toBeInTheDocument();
		await settleAnimations(container);
		await checkA11y(container);
	});

	it('names several credentials on the row, and in the reveal says what its rules cover', async () => {
		renderRow(makeTile(), { accountCount: 2 });
		expect(screen.getByTestId('tile-accounts-badge')).toHaveTextContent('2 credentials');
		fireEvent.click(chevron('Slack'));
		const reveal = await screen.findByTestId('api-row-reveal');
		// The long explanation stays on the tag's tooltip; the reveal's footer
		// says only what the rules here cover.
		expect(within(reveal).queryByText(/2 credentials for Slack\. Unless/)).toBeNull();
		expect(reveal).toHaveTextContent(
			'One of 2 credentials for Slack — its rules apply only when a call uses it.',
		);
	});

	it('a mouse click on the row pins it at once and a second unpins it; neither opens the sheet', async () => {
		const onOpen = vi.fn();
		renderRow(makeTile(), { onOpen });
		fireEvent.pointerEnter(row(), { pointerType: 'mouse' });

		clickRow('Slack');
		expect(row()).toHaveAttribute('data-revealed', 'true');
		expect(row()).toHaveAttribute('data-pinned', 'true');
		clickRow('Slack');
		await waitFor(() => expect(screen.queryByTestId('api-row-reveal')).toBeNull());
		expect(row()).not.toHaveAttribute('data-pinned');
		expect(onOpen).not.toHaveBeenCalled();
	});

	it('a click on a row hover has opened pins it: leaving keeps it, a second click folds it', async () => {
		await onFakeClock((advance) => {
			renderRow(makeTile());
			fireEvent.pointerEnter(row(), { pointerType: 'mouse' });
			fireEvent.pointerMove(row(), { pointerType: 'mouse', movementX: 2 });
			advance(HOVER_INTENT.openDelayMs + 10);
			expect(row()).toHaveAttribute('data-revealed', 'true');

			clickRow('Slack');
			expect(row()).toHaveAttribute('data-pinned', 'true');
			fireEvent.pointerLeave(row(), { pointerType: 'mouse' });
			// Past the layout settle and the leave grace: a pin holds.
			advance(HOVER_INTENT.layoutSettleMs + HOVER_INTENT.closeGraceMs + 20);
			expect(row()).toHaveAttribute('data-revealed', 'true');

			fireEvent.pointerEnter(row(), { pointerType: 'mouse' });
			clickRow('Slack');
			advance(FOLD_MS);
			expect(screen.queryByTestId('api-row-reveal')).toBeNull();
		});
	});

	it('two rows pin at once: clicking a second keeps the first open', async () => {
		renderRows();
		clickRow('Slack');
		clickRow('GitHub');
		expect(rowOf('Slack')).toHaveAttribute('data-pinned', 'true');
		expect(rowOf('GitHub')).toHaveAttribute('data-pinned', 'true');
		expect(await within(rowOf('Slack')).findByTestId('api-row-reveal')).toBeInTheDocument();
		expect(await within(rowOf('GitHub')).findByTestId('api-row-reveal')).toBeInTheDocument();
	});

	it('a second click unpins only that row', async () => {
		renderRows();
		clickRow('Slack');
		clickRow('GitHub');
		clickRow('Slack');
		await waitFor(() => expect(revealOf('Slack')).toBeNull());
		expect(rowOf('GitHub')).toHaveAttribute('data-pinned', 'true');
		expect(revealOf('GitHub')).toBeInTheDocument();
	});

	it('hover previews an unpinned row beside the pins, and folding it leaves them open', async () => {
		await onFakeClock((advance) => {
			renderRows();
			clickRow('Slack');
			clickRow('GitHub');
			const stripe = rowOf('Stripe');
			fireEvent.pointerEnter(stripe, { pointerType: 'mouse' });
			fireEvent.pointerMove(stripe, { pointerType: 'mouse', movementX: 2 });
			advance(HOVER_INTENT.openDelayMs + 10);
			expect(rowOf('Stripe')).toHaveAttribute('data-revealed', 'true');

			advance(HOVER_INTENT.layoutSettleMs + 20);
			fireEvent.pointerLeave(rowOf('Stripe'), { pointerType: 'mouse' });
			advance(HOVER_INTENT.closeGraceMs + HOVER_INTENT.handoffMaxMs + FOLD_MS);
			expect(revealOf('Stripe')).toBeNull();
			expect(rowOf('Slack')).toHaveAttribute('data-revealed', 'true');
			expect(rowOf('GitHub')).toHaveAttribute('data-revealed', 'true');
		});
	});

	it('hovering a pinned row neither re-opens nor folds it', async () => {
		await onFakeClock((advance) => {
			renderRows();
			clickRow('Slack');
			const slack = rowOf('Slack');
			fireEvent.pointerEnter(slack, { pointerType: 'mouse' });
			fireEvent.pointerMove(slack, { pointerType: 'mouse', movementX: 3 });
			advance(HOVER_INTENT.layoutSettleMs + 20);
			fireEvent.pointerLeave(slack, { pointerType: 'mouse' });
			advance(HOVER_INTENT.closeGraceMs + HOVER_INTENT.handoffMaxMs + FOLD_MS);
			expect(slack).toHaveAttribute('data-pinned', 'true');
			expect(revealOf('Slack')).toBeInTheDocument();
		});
	});

	it('Escape unpins the latest pin first', async () => {
		renderRows();
		clickRow('Slack');
		clickRow('Stripe');
		clickRow('GitHub');
		fireEvent.keyDown(document.body, { key: 'Escape' });
		await waitFor(() => expect(revealOf('GitHub')).toBeNull());
		expect(rowOf('Slack')).toHaveAttribute('data-pinned', 'true');
		expect(rowOf('Stripe')).toHaveAttribute('data-pinned', 'true');
		fireEvent.keyDown(document.body, { key: 'Escape' });
		await waitFor(() => expect(revealOf('Stripe')).toBeNull());
		expect(rowOf('Slack')).toHaveAttribute('data-pinned', 'true');
	});

	it('a click while the hover open is pending pins the row at once; Escape folds it', async () => {
		renderRow(makeTile());
		fireEvent.pointerEnter(row(), { pointerType: 'mouse' });
		fireEvent.pointerMove(row(), { pointerType: 'mouse', movementX: 2 });
		clickRow('Slack');
		expect(row()).toHaveAttribute('data-revealed', 'true');
		expect(screen.getByTestId('api-row-reveal')).toBeInTheDocument();

		fireEvent.keyDown(header('Slack'), { key: 'Escape' });
		expect(row()).not.toHaveAttribute('data-revealed');
		await waitFor(() => expect(screen.queryByTestId('api-row-reveal')).toBeNull());
	});

	it('on touch, a tap toggles the row and never opens the sheet', async () => {
		const onOpen = vi.fn();
		renderRows({ width: 390, onOpen });
		tapRow('Slack');
		expect(await within(rowOf('Slack')).findByTestId('api-row-reveal')).toBeInTheDocument();
		tapRow('Slack');
		await waitFor(() => expect(revealOf('Slack')).toBeNull());
		expect(onOpen).not.toHaveBeenCalled();
	});

	it('on touch, taps open several rows at once, each toggling on its own', async () => {
		renderRows({ width: 390 });
		tapRow('Slack');
		tapRow('GitHub');
		expect(await within(rowOf('Slack')).findByTestId('api-row-reveal')).toBeInTheDocument();
		expect(await within(rowOf('GitHub')).findByTestId('api-row-reveal')).toBeInTheDocument();
		tapRow('Slack');
		await waitFor(() => expect(revealOf('Slack')).toBeNull());
		expect(revealOf('GitHub')).toBeInTheDocument();
	});

	it('on touch, taps inside the grown row neither fold it nor open the sheet', async () => {
		const onOpen = vi.fn();
		renderRows({ width: 390, onOpen });
		tapRow('Slack');
		const reveal = await within(rowOf('Slack')).findByTestId('api-row-reveal');

		// Taps on the body move focus out of the row — that must not fold it.
		for (const target of [
			within(reveal).getByTestId('row-usage-line'),
			within(reveal).getByRole('heading', { name: /Access/ }),
		]) {
			fireEvent.pointerDown(target, { pointerType: 'touch' });
			fireEvent.click(target);
			(document.activeElement as HTMLElement | null)?.blur();
		}
		expect(revealOf('Slack')).toBeInTheDocument();
		expect(onOpen).not.toHaveBeenCalled();
	});

	it('opening the sheet unpins just that row, and the focus handed back does not grow it', async () => {
		const onOpen = vi.fn();
		renderRows({ onOpen });
		clickRow('Slack');
		clickRow('GitHub');
		const reveal = await within(rowOf('Slack')).findByTestId('api-row-reveal');

		fireEvent.click(within(reveal).getByRole('button', { name: 'Manage access' }));
		expect(onOpen).toHaveBeenCalledWith('Slack');
		await waitFor(() => expect(revealOf('Slack')).toBeNull());
		expect(rowOf('Slack')).not.toHaveAttribute('data-pinned');
		// The other pin stays for when the sheet closes.
		expect(rowOf('GitHub')).toHaveAttribute('data-pinned', 'true');
		// Focus went to the row's own Manage access — where the sheet returns it.
		expect(manageButton('Slack')).toHaveFocus();

		// The sheet closing hands focus back to the row: it stays at rest.
		await onFakeClock((advance) => {
			manageButton('Slack').blur();
			manageButton('Slack').focus();
			advance(HOVER_INTENT.openDelayMs + FOLD_MS);
			expect(revealOf('Slack')).toBeNull();
		});
	});
});

describe('ApiRow controls', () => {
	it('the chevron toggles only its row, turning and saying so; it never opens the sheet', async () => {
		const onOpen = vi.fn();
		renderRows({ onOpen });
		const turn = () => within(chevron('Slack')).getByTestId('row-toggle-chevron');
		expect(chevron('Slack')).toHaveAttribute('aria-expanded', 'false');
		expect(chevron('Slack')).not.toHaveAttribute('aria-controls');
		expect(turn()).not.toHaveClass('rotate-90');

		clickRow('GitHub');
		fireEvent.click(chevron('Slack'));
		expect(chevron('Slack')).toHaveAttribute('aria-expanded', 'true');
		expect(chevron('Slack')).toHaveAttribute('data-state', 'open');
		expect(turn()).toHaveClass('rotate-90');
		const reveal = await within(rowOf('Slack')).findByTestId('api-row-reveal');
		const region = document.getElementById(chevron('Slack').getAttribute('aria-controls')!);
		expect(region).toContainElement(reveal);
		// The pointer's header is no second control: out of the a11y tree.
		expect(header('Slack')).toHaveAttribute('aria-hidden', 'true');
		expect(header('Slack')).not.toHaveAttribute('aria-expanded');

		fireEvent.click(chevron('Slack'));
		expect(chevron('Slack')).toHaveAttribute('aria-expanded', 'false');
		expect(turn()).not.toHaveClass('rotate-90');
		await waitFor(() => expect(revealOf('Slack')).toBeNull());
		expect(rowOf('GitHub')).toHaveAttribute('data-pinned', 'true');
		expect(onOpen).not.toHaveBeenCalled();
	});

	it('Manage access opens the sheet without toggling the row, named for its API and credential', async () => {
		const onOpen = vi.fn();
		const { container } = renderRows({ onOpen });
		const manage = manageButton('Slack');
		expect(manage).toHaveAccessibleName('Manage access for Slack (Slack bot token)');
		expect(manage).not.toHaveAttribute('title');
		expect(manage).toHaveAttribute('aria-haspopup', 'dialog');

		// The shared tooltip names it.
		fireEvent.focus(manage);
		expect(await screen.findByRole('tooltip')).toHaveTextContent('Manage access');

		fireEvent.pointerDown(manage, { pointerType: 'mouse' });
		fireEvent.click(manage);
		expect(onOpen).toHaveBeenCalledWith('Slack');
		expect(rowOf('Slack')).not.toHaveAttribute('data-pinned');
		await settleAnimations(container);
		await checkA11y(container);
	});

	it('Manage access on a Blocked row opens the sheet on its rules', () => {
		const onOpen = vi.fn();
		const onOpenRules = vi.fn();
		renderRow(makeTile(), { rules: { total: 0, allow: 0, deny: 0 }, onOpen, onOpenRules });
		fireEvent.click(manageButton('Slack'));
		expect(onOpenRules).toHaveBeenCalledWith('Slack');
		expect(onOpen).not.toHaveBeenCalled();
	});

	it('keyboard: Enter or Space on the chevron toggles the pin; Enter on Manage access opens the sheet', async () => {
		const onOpen = vi.fn();
		const user = userEvent.setup();
		renderRows({ onOpen });

		chevron('Slack').focus();
		await user.keyboard('{Enter}');
		expect(rowOf('Slack')).toHaveAttribute('data-pinned', 'true');
		expect(await within(rowOf('Slack')).findByTestId('api-row-reveal')).toBeInTheDocument();
		await user.keyboard(' ');
		expect(rowOf('Slack')).not.toHaveAttribute('data-pinned');
		// Unpinned from the keyboard, it folds.
		await waitFor(() => expect(revealOf('Slack')).toBeNull());

		chevron('GitHub').focus();
		await user.keyboard(' ');
		expect(rowOf('GitHub')).toHaveAttribute('data-pinned', 'true');
		await user.keyboard('{Enter}');
		expect(rowOf('GitHub')).not.toHaveAttribute('data-pinned');
		expect(onOpen).not.toHaveBeenCalled();

		manageButton('Stripe').focus();
		await user.keyboard('{Enter}');
		expect(onOpen).toHaveBeenCalledWith('Stripe');
	});

	it('Tab reaches Manage access, then the chevron: the header is one action, not a stop', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<>
				<button type="button">before</button>
				<Rows tiles={[makeTile()]} />
			</>,
		);
		expect(header('Slack')).toHaveAttribute('tabindex', '-1');
		expect(header('Slack')).toHaveAttribute('aria-hidden', 'true');
		screen.getByRole('button', { name: 'before' }).focus();
		await user.tab();
		expect(manageButton('Slack')).toHaveFocus();
		await user.tab();
		expect(chevron('Slack')).toHaveFocus();
		// One control for the pin in the accessibility tree: the chevron.
		expect(
			within(row())
				.getAllByRole('button', { name: /details/ })
				.map((b) => b.dataset.testid),
		).toEqual(['row-toggle']);
	});

	it('an empty pick from a capped feed never reads as "No calls yet"', async () => {
		// The agent's newest 100 calls all went elsewhere, and older ones exist.
		const { unmount } = renderRow(makeTile(), {
			activity: { ...QUIET, scanned: { count: 100, hasMore: true } },
		});
		fireEvent.click(chevron('Slack'));
		let section = within(await screen.findByTestId('api-row-reveal')).getByRole('region', {
			name: 'Recent calls',
		});
		expect(section).toHaveTextContent('None in the newest 100 calls.');
		expect(section).not.toHaveTextContent('No calls yet');
		expect(within(section).getByRole('link', { name: 'See all in Monitor' })).toHaveAttribute(
			'href',
			'/monitor?show=calls&actor_id=agt_1&actor_type=agent',
		);
		unmount();

		// The 7-day rollup counts calls the feed's window never reached.
		renderRow(makeTile(), {
			activity: { ...BUSY, calls: [], scanned: { count: 100, hasMore: false } },
		});
		fireEvent.click(chevron('Slack'));
		section = within(await screen.findByTestId('api-row-reveal')).getByRole('region', {
			name: 'Recent calls',
		});
		expect(section).toHaveTextContent('None in the newest 100 calls.');
	});

	it('an empty pick from the whole history says "No calls yet"', async () => {
		renderRow(makeTile(), { activity: { ...QUIET, scanned: { count: 12, hasMore: false } } });
		fireEvent.click(chevron('Slack'));
		const reveal = await screen.findByTestId('api-row-reveal');
		expect(within(reveal).getByRole('region', { name: 'Recent calls' })).toHaveTextContent(
			'No calls yet.',
		);
	});

	it('an untraced call offers no trace link', async () => {
		const [call] = BUSY.calls!;
		renderRow(makeTile(), { activity: { ...BUSY, calls: [{ ...call, traceId: null }] } });
		fireEvent.click(chevron('Slack'));
		const reveal = await screen.findByTestId('api-row-reveal');
		expect(within(reveal).getByTestId('row-recent-call')).toBeInTheDocument();
		expect(within(reveal).queryByRole('link', { name: /Open trace/ })).toBeNull();
	});

	it('a quiet blocked row says why it has no calls', async () => {
		renderRow(makeTile(), { rules: { total: 0, allow: 0, deny: 0 } });
		fireEvent.click(chevron('Slack'));
		const reveal = await screen.findByTestId('api-row-reveal');
		expect(reveal).toHaveTextContent('default-deny refuses every call');
	});
});

describe('ApiRow controls are a hover dead zone', () => {
	const BLOCKED = { total: 0, allow: 0, deny: 0 } as const;
	const mouse = { pointerType: 'mouse' } as const;
	const statusButton = () => within(rowOf('Slack')).getByTestId('tile-status-blocked');
	const zones: [string, () => HTMLElement][] = [
		['Manage access', () => manageButton('Slack')],
		['the chevron', () => chevron('Slack')],
		['the Blocked status', () => statusButton()],
	];

	/** Watch the row's revealed state across every frame and event. */
	function watchRevealed(el: HTMLElement) {
		const seen: boolean[] = [];
		const observer = new MutationObserver(() => seen.push(el.hasAttribute('data-revealed')));
		observer.observe(el, { attributes: true, subtree: true, childList: true });
		return {
			seen,
			stop: () => observer.disconnect(),
		};
	}

	for (const [name, target] of zones) {
		it(`resting on ${name} never opens the row, even past the delay`, () => {
			vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
			try {
				renderRow(makeTile(), { rules: BLOCKED });
				fireEvent.pointerEnter(row(), mouse);
				fireEvent.pointerMove(target(), { ...mouse, movementX: 2 });
				vi.advanceTimersByTime(HOVER_INTENT.openDelayMs * 5);
				fireEvent.pointerMove(target(), { ...mouse, movementY: 1 });
				vi.advanceTimersByTime(HOVER_INTENT.openDelayMs * 5);
				expect(row()).not.toHaveAttribute('data-revealed');
				expect(screen.queryByTestId('api-row-reveal')).toBeNull();
			} finally {
				vi.useRealTimers();
			}
		});

		it(`moving from the body onto ${name} cancels a pending open; back on the body restarts the wait`, async () => {
			vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
			try {
				renderRow(makeTile(), { rules: BLOCKED });
				fireEvent.pointerEnter(row(), mouse);
				fireEvent.pointerMove(row(), { ...mouse, movementX: 2 });
				vi.advanceTimersByTime(HOVER_INTENT.openDelayMs - 50);
				fireEvent.pointerMove(target(), { ...mouse, movementX: 2 });
				vi.advanceTimersByTime(HOVER_INTENT.openDelayMs * 2);
				expect(row()).not.toHaveAttribute('data-revealed');

				fireEvent.pointerMove(row(), { ...mouse, movementX: -2 });
				vi.advanceTimersByTime(HOVER_INTENT.openDelayMs - 20);
				expect(row()).not.toHaveAttribute('data-revealed');
				vi.advanceTimersByTime(40);
			} finally {
				vi.useRealTimers();
			}
			await waitFor(() => expect(row()).toHaveAttribute('data-revealed', 'true'));
		});
	}

	it('keyboard focus on the controls previews nothing; the chevron opens it', async () => {
		renderWithProviders(
			<>
				<button type="button">before</button>
				<Rows tiles={[makeTile()]} rules={BLOCKED} />
			</>,
		);
		// Shift+Tab from after the row lands on the chevron first.
		const user = userEvent.setup();
		chevron('Slack').focus();
		await user.keyboard('{Shift>}{Tab}{/Shift}');
		expect(manageButton('Slack')).toHaveFocus();
		await user.keyboard('{Shift>}{Tab}{/Shift}');
		expect(statusButton()).toHaveFocus();
		// The header is no stop: the next one is outside the row.
		await user.keyboard('{Shift>}{Tab}{/Shift}');
		expect(screen.getByRole('button', { name: 'before' })).toHaveFocus();
		// Back on a control, past the hover delay: still nothing previews.
		await onFakeClock((advance) => {
			act(() => statusButton().focus());
			advance(HOVER_INTENT.openDelayMs * 2);
			expect(row()).not.toHaveAttribute('data-revealed');
		});
		chevron('Slack').focus();
		await user.keyboard('{Enter}');
		expect(row()).toHaveAttribute('data-revealed', 'true');
	});

	it('clicking Manage access after resting on it opens the sheet with no reveal at any point', async () => {
		const onOpen = vi.fn();
		renderRow(makeTile(), { onOpen });
		const watch = watchRevealed(row());
		const manage = manageButton('Slack');
		await onFakeClock((advance) => {
			fireEvent.pointerEnter(row(), mouse);
			fireEvent.pointerMove(manage, { ...mouse, movementX: 2 });
			advance(HOVER_INTENT.openDelayMs * 3);
			fireEvent.pointerDown(manage, mouse);
			manage.focus();
			fireEvent.pointerUp(manage, mouse);
			fireEvent.click(manage);
			advance(HOVER_INTENT.openDelayMs + FOLD_MS);
		});
		watch.stop();
		expect(onOpen).toHaveBeenCalledWith('Slack');
		expect(watch.seen).not.toContain(true);
		expect(row()).not.toHaveAttribute('data-revealed');
		expect(screen.queryByTestId('api-row-reveal')).toBeNull();
	});

	it('clicking the Blocked status opens the rules with no reveal at any point', async () => {
		const onOpen = vi.fn();
		const onOpenRules = vi.fn();
		renderRow(makeTile(), { rules: BLOCKED, onOpen, onOpenRules });
		const watch = watchRevealed(row());
		const status = statusButton();
		await onFakeClock((advance) => {
			fireEvent.pointerEnter(row(), mouse);
			fireEvent.pointerMove(status, { ...mouse, movementX: 2 });
			advance(HOVER_INTENT.openDelayMs * 3);
			fireEvent.pointerDown(status, mouse);
			status.focus();
			fireEvent.pointerUp(status, mouse);
			fireEvent.click(status);
			advance(HOVER_INTENT.openDelayMs + FOLD_MS);
		});
		watch.stop();
		expect(onOpenRules).toHaveBeenCalledWith('Slack');
		expect(onOpen).not.toHaveBeenCalled();
		expect(watch.seen).not.toContain(true);
		expect(screen.queryByTestId('api-row-reveal')).toBeNull();
	});

	it('the chevron still toggles the pin', () => {
		renderRow(makeTile());
		fireEvent.pointerEnter(row(), mouse);
		fireEvent.pointerMove(chevron('Slack'), { ...mouse, movementX: 2 });
		fireEvent.click(chevron('Slack'));
		expect(row()).toHaveAttribute('data-pinned', 'true');
		expect(row()).toHaveAttribute('data-revealed', 'true');
	});
});

describe('ApiRow chevron follows what is visibly open', () => {
	const mouse = { pointerType: 'mouse' } as const;
	const turned = (title: string) =>
		within(chevron(title)).getByTestId('row-toggle-chevron').classList.contains('rotate-90');

	/** Rest the mouse on a row until hover intent opens it (fake clock). */
	function preview(title: string, advance: (ms: number) => void) {
		fireEvent.pointerEnter(rowOf(title), mouse);
		fireEvent.pointerMove(rowOf(title), { ...mouse, movementX: 2 });
		advance(HOVER_INTENT.openDelayMs + 10);
		expect(rowOf(title)).toHaveAttribute('data-revealed', 'true');
	}

	it('a hover preview turns it and says expanded; leaving turns it back', async () => {
		await onFakeClock((advance) => {
			renderRows();
			expect(turned('Slack')).toBe(false);
			preview('Slack', advance);
			expect(turned('Slack')).toBe(true);
			expect(chevron('Slack')).toHaveAttribute('aria-expanded', 'true');
			expect(chevron('Slack')).toHaveAttribute('data-state', 'open');
			expect(rowOf('Slack')).not.toHaveAttribute('data-pinned');

			advance(HOVER_INTENT.layoutSettleMs + 20);
			fireEvent.pointerLeave(rowOf('Slack'), mouse);
			advance(HOVER_INTENT.closeGraceMs + HOVER_INTENT.handoffMaxMs);
			expect(turned('Slack')).toBe(false);
			expect(chevron('Slack')).toHaveAttribute('aria-expanded', 'false');
		});
	});

	it('a click on the chevron during a preview pins it — it stays open and turned', async () => {
		await onFakeClock((advance) => {
			renderRows();
			preview('Slack', advance);
			fireEvent.pointerDown(chevron('Slack'), mouse);
			fireEvent.click(chevron('Slack'));
			expect(rowOf('Slack')).toHaveAttribute('data-pinned', 'true');
			expect(rowOf('Slack')).toHaveAttribute('data-revealed', 'true');
			expect(turned('Slack')).toBe(true);
			// Pinned, it holds through the leave.
			advance(HOVER_INTENT.layoutSettleMs + 20);
			fireEvent.pointerLeave(rowOf('Slack'), mouse);
			advance(HOVER_INTENT.closeGraceMs + HOVER_INTENT.handoffMaxMs + FOLD_MS);
			expect(turned('Slack')).toBe(true);
		});
	});

	it('a click on a pinned row’s chevron closes it and turns it back', async () => {
		renderRows();
		fireEvent.click(chevron('Slack'));
		expect(turned('Slack')).toBe(true);
		fireEvent.click(chevron('Slack'));
		expect(turned('Slack')).toBe(false);
		expect(chevron('Slack')).toHaveAttribute('aria-expanded', 'false');
		await waitFor(() => expect(revealOf('Slack')).toBeNull());
	});
});
