import { describe, it, expect, vi, afterEach } from 'vitest';
import { act, render, screen, userEvent } from '@/__tests__/test-utils';
import { TaskComposer } from '@/modules/agents/components/landing/act1/StoryParts';
import {
	COMPOSER_HOLD_MS,
	COMPOSER_MS_PER_CHAR,
	WHY_SCRIPT,
	WHY_TASK,
} from '@/modules/agents/components/landing/act1/script';

afterEach(() => {
	vi.useRealTimers();
});

describe('TaskComposer', () => {
	it('types the task, holds it fully visible, then presses send', () => {
		vi.useFakeTimers();
		render(<TaskComposer reduced={false} />);
		const send = screen.getByTestId('composer-send');
		expect(send).toHaveAttribute('data-state', 'typing');
		act(() => {
			vi.advanceTimersByTime((WHY_TASK.length + 2) * COMPOSER_MS_PER_CHAR);
		});
		expect(send).toHaveAttribute('data-state', 'ready');
		// Still readable, not sent, well into the hold.
		act(() => {
			vi.advanceTimersByTime(COMPOSER_HOLD_MS - 600);
		});
		expect(send).toHaveAttribute('data-state', 'ready');
		act(() => {
			vi.advanceTimersByTime(400);
		});
		expect(send).toHaveAttribute('data-state', 'pressed');
	});

	it('the send button is a real button: click or Enter sends now', async () => {
		const user = userEvent.setup();
		const onSend = vi.fn();
		render(<TaskComposer reduced={false} onSend={onSend} />);
		const send = screen.getByRole('button', { name: 'Send the task to research-bot' });
		await user.click(send);
		expect(onSend).toHaveBeenCalledTimes(1);
		send.focus();
		await user.keyboard('{Enter}');
		expect(onSend).toHaveBeenCalledTimes(2);
	});

	it('the intro beat outlasts typing plus the hold', () => {
		const intro = WHY_SCRIPT.beats[0];
		expect(intro.id).toBe('task');
		expect(intro.durationMs).toBeGreaterThanOrEqual(
			WHY_TASK.length * COMPOSER_MS_PER_CHAR + COMPOSER_HOLD_MS,
		);
	});
});
