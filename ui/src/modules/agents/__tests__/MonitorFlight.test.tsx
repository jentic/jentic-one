import { describe, it, expect } from 'vitest';
import { page } from 'vitest/browser';
import { render, screen, userEvent, waitFor } from '@/__tests__/test-utils';
import { WhyAct } from '@/modules/agents/components/landing/act1/WhyAct';
import { WHY_LOOP_FROM, WHY_SCRIPT } from '@/modules/agents/components/landing/act1/script';

/**
 * The Monitor hand-off, measured in a real browser layout at a desktop width:
 * when an event lands in the Monitor, a short bridge is drawn only in the
 * gutter between the lane's API column and the Monitor item, the call's badge
 * flies along it, and the item appears labelled once the badge lands.
 */
describe('Act 1 Monitor hand-off', () => {
	it('flies the badge across a gutter-only bridge, then labels the landed item', async () => {
		await page.viewport(1440, 900);
		const user = userEvent.setup();
		render(<WhyAct reducedMotion={false} onNext={() => {}} />);
		// Send the task now: the story jumps to the registration request.
		await user.click(screen.getByRole('button', { name: 'Send the task to research-bot' }));
		await waitFor(
			() => expect(screen.getByTestId('why-stage')).toHaveAttribute('data-beat', 'register'),
			{ timeout: 3000 },
		);
		const bridge = await waitFor(
			() => {
				const path = screen.getByTestId('monitor-bridge') as unknown as SVGPathElement;
				expect(path.getTotalLength()).toBeGreaterThan(20);
				return path;
			},
			{ timeout: 3000 },
		);
		expect(screen.getByTestId('monitor-flight-badge')).toBeInTheDocument();
		// In flight, the Notifications item waits for the badge.
		const item = screen
			.getByRole('region', { name: 'Notifications' })
			.querySelector<HTMLElement>('li[data-landing]');
		expect(item).toHaveAttribute('data-landing', 'flying');

		// The bridge lives in the gutter: right of every API box, left of the item.
		const box = bridge.getBoundingClientRect();
		const apiRight = Math.max(
			...[...document.querySelectorAll('[data-with-api]')].map(
				(a) => a.getBoundingClientRect().right,
			),
		);
		expect(box.left).toBeGreaterThanOrEqual(apiRight - 1);
		expect(box.right).toBeLessThanOrEqual(item!.getBoundingClientRect().left + 1);
		for (const stage of document.querySelectorAll('[data-stage]'))
			expect(stage.getBoundingClientRect().right).toBeLessThan(box.left);

		// It lands: the item shows, with its short label, and the flight clears.
		await waitFor(() => expect(item).toHaveAttribute('data-landing', 'landed'), {
			timeout: 3000,
		});
		expect(screen.getByTestId('monitor-label')).toHaveTextContent('Needs you');
		expect(item).toContainElement(screen.getByTestId('monitor-label'));
		await waitFor(() => expect(screen.queryByTestId('monitor-flight')).toBeNull(), {
			timeout: 3000,
		});
		// The one live region is the narration caption, which says the same.
		expect(screen.getByTestId('landing-caption')).toHaveTextContent(
			WHY_SCRIPT.beats[WHY_LOOP_FROM].caption,
		);
	});

	it.each([1280, 1440, 1600])(
		'at %ipx the key pills sit whole, centred under the agent',
		async (width) => {
			await page.viewport(width, 900);
			render(<WhyAct reducedMotion onNext={() => {}} />);
			for (const [lane, pillId] of [
				['without-lane', 'key-pill'],
				['with-lane', 'no-keys-pill'],
			] as const) {
				const root = screen.getByTestId(lane);
				const svg = root.querySelector('svg')!.getBoundingClientRect();
				const pill = root
					.querySelector(`[data-testid="${pillId}"] rect`)!
					.getBoundingClientRect();
				const agent = root.querySelector('[data-agent-mark]')!.getBoundingClientRect();
				expect(pill.left).toBeGreaterThanOrEqual(svg.left);
				expect(pill.left).toBeGreaterThanOrEqual(root.getBoundingClientRect().left);
				expect(
					Math.abs(pill.left + pill.width / 2 - (agent.left + agent.width / 2)),
				).toBeLessThan(2);
			}
		},
	);

	it('under reduced motion the item and its label simply appear: no flight', async () => {
		await page.viewport(1440, 900);
		const user = userEvent.setup();
		render(<WhyAct reducedMotion onNext={() => {}} />);
		await user.click(screen.getByRole('button', { name: 'Next step' }));
		await waitFor(() =>
			expect(screen.getByTestId('monitor-label')).toHaveTextContent('Needs you'),
		);
		expect(screen.queryByTestId('monitor-flight')).toBeNull();
		expect(screen.queryByTestId('monitor-bridge')).toBeNull();
	});
});
