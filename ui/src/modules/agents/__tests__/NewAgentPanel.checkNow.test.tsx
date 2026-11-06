/**
 * "Check now" when the roster re-read fails: the button returns from "Checking…",
 * and a panel closed inside its half-second floor leaves no write behind.
 * Own file because the roster endpoint is broken for the whole case.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { MotionConfig } from 'framer-motion';
import { page } from 'vitest/browser';
import { worker } from '@/mocks/browser';
import {
	renderWithProviders,
	screen,
	waitFor,
	within,
	userEvent,
	createErrorHandler,
} from '@/__tests__/test-utils';
import { setToken } from '@/shared/api';
import { Toaster } from '@/shared/ui';
import { resetAgentsStore } from '@/modules/agents/mocks/handlers';
import AgentsPage from '@/modules/agents/pages/AgentsPage';

type User = ReturnType<typeof userEvent.setup>;

const panel = () => screen.getByRole('dialog', { name: 'New agent' });
const checkNow = () => within(panel()).getByTestId('footer-check-now');

async function openRegister(user: User) {
	await screen.findByTestId('agent-dock');
	await user.click(screen.getByRole('button', { name: 'New agent' }));
	const sheet = await screen.findByRole('dialog', { name: 'New agent' });
	await waitFor(() => expect(within(sheet).getByLabelText('Agent name')).toHaveFocus());
	return sheet;
}

function renderPage() {
	return renderWithProviders(
		<MotionConfig reducedMotion="never">
			<AgentsPage />
			<Toaster />
		</MotionConfig>,
	);
}

describe('New agent panel — "Check now" when the re-read fails', () => {
	beforeEach(async () => {
		await page.viewport(1440, 900);
		setToken('test-token');
		window.localStorage.clear();
		resetAgentsStore();
	});

	it('comes back off "Checking…" and can be asked again', async () => {
		const user = userEvent.setup();
		renderPage();
		const sheet = await openRegister(user);

		// The roster goes down only now, so it is the re-read that fails, not the page.
		worker.use(createErrorHandler('get', '*/agents', { status: 503 }));
		await user.click(checkNow());
		await waitFor(() => expect(checkNow()).toHaveTextContent('Checking…'));

		await waitFor(() => expect(checkNow()).toHaveTextContent('Check now'), { timeout: 4000 });
		expect(checkNow()).toBeEnabled();
		// The roster query owns the error, so the button says nothing about it.
		expect(within(sheet).getByTestId('register-status')).toHaveTextContent(
			'Listening for new agents…',
		);

		worker.resetHandlers();
		await user.click(checkNow());
		await waitFor(() => expect(checkNow()).toHaveTextContent('Check now'), { timeout: 4000 });
		expect(checkNow()).toBeEnabled();
	});

	it('a panel closed mid-check settles quietly, and reopens idle', async () => {
		const user = userEvent.setup();
		const errors: unknown[] = [];
		const onError = (e: ErrorEvent) => errors.push(e.error ?? e.message);
		const onRejection = (e: PromiseRejectionEvent) => errors.push(e.reason);
		window.addEventListener('error', onError);
		window.addEventListener('unhandledrejection', onRejection);
		try {
			renderPage();
			const sheet = await openRegister(user);

			worker.use(createErrorHandler('get', '*/agents', { status: 503 }));
			// Closing inside the half-second floor: the check settles after unmount, so its write is guarded.
			await user.click(checkNow());
			await user.click(within(sheet).getByRole('button', { name: 'Cancel' }));
			await waitFor(() =>
				expect(screen.queryByRole('dialog', { name: 'New agent' })).toBeNull(),
			);
			await new Promise((r) => setTimeout(r, 900));
			expect(errors).toEqual([]);

			worker.resetHandlers();
			const reopened = await openRegister(user);
			expect(within(reopened).getByTestId('footer-check-now')).toHaveTextContent('Check now');
			expect(within(reopened).getByTestId('footer-check-now')).toBeEnabled();
		} finally {
			window.removeEventListener('error', onError);
			window.removeEventListener('unhandledrejection', onRejection);
		}
	});
});
