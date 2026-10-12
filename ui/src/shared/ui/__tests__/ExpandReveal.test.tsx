import { useState } from 'react';
import { describe, it, expect } from 'vitest';
import { MotionConfig } from 'framer-motion';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ExpandReveal } from '@/shared/ui/ExpandReveal';

function Harness({ reduced = false }: { reduced?: boolean }) {
	const [open, setOpen] = useState(false);
	const [events, setEvents] = useState<string[]>([]);
	return (
		<MotionConfig reducedMotion={reduced ? 'always' : 'never'}>
			<button type="button" onClick={() => setOpen((o) => !o)}>
				toggle
			</button>
			<output data-testid="events">{events.join(',')}</output>
			<ExpandReveal
				open={open}
				data-testid="reveal"
				onOpened={() => setEvents((e) => [...e, 'opened'])}
				onClosed={() => setEvents((e) => [...e, 'closed'])}
			>
				<p style={{ height: 120, margin: 0 }}>Body</p>
			</ExpandReveal>
		</MotionConfig>
	);
}

const toggle = () => fireEvent.click(screen.getByRole('button', { name: 'toggle' }));
const reveal = () => screen.queryByTestId('reveal');
const events = () => screen.getByTestId('events').textContent;

describe('ExpandReveal', () => {
	it('renders nothing while shut', () => {
		render(<Harness />);
		expect(reveal()).toBeNull();
		expect(screen.queryByText('Body')).toBeNull();
	});

	it('mounts collapsed, then grows its track to the content', async () => {
		render(<Harness />);
		toggle();
		// Mounted at once (its reads start), collapsed for its first paint.
		expect(screen.getByText('Body')).toBeInTheDocument();
		expect(reveal()).toHaveAttribute('data-state', 'mounting');
		expect(reveal()!.style.gridTemplateRows).toBe('0fr');
		await waitFor(() => expect(reveal()).toHaveAttribute('data-state', 'open'));
		expect(reveal()!.style.gridTemplateRows).toBe('1fr');
		await waitFor(() => expect(events()).toBe('opened'));
		expect(reveal()!.getBoundingClientRect().height).toBeCloseTo(120, 0);
	});

	it('folds before it unmounts, and its content is inert while it folds', async () => {
		render(<Harness />);
		toggle();
		await waitFor(() => expect(events()).toBe('opened'));
		toggle();
		// Holds still for a frame or two (so a sibling opening moves in step)…
		expect(reveal()).toHaveAttribute('data-state', 'leaving');
		await waitFor(() => expect(reveal()).toHaveAttribute('data-state', 'closing'));
		expect(reveal()!.style.gridTemplateRows).toBe('0fr');
		expect(reveal()!.firstElementChild).toHaveAttribute('inert');
		await waitFor(() => expect(reveal()).toBeNull());
		await waitFor(() => expect(events()).toBe('opened,closed'));
	});

	it('reopening mid-fold runs back without remounting', async () => {
		render(<Harness />);
		toggle();
		await waitFor(() => expect(events()).toBe('opened'));
		const body = screen.getByText('Body');
		toggle();
		toggle();
		expect(reveal()).toHaveAttribute('data-state', 'open');
		expect(screen.getByText('Body')).toBe(body);
	});

	it('is instant under reduced motion', async () => {
		render(<Harness reduced />);
		toggle();
		expect(reveal()).toHaveAttribute('data-state', 'open');
		expect(reveal()!.style.transition).toBe('none');
		await waitFor(() => expect(events()).toBe('opened'));
		act(() => toggle());
		expect(reveal()).toBeNull();
		await waitFor(() => expect(events()).toBe('opened,closed'));
	});
});
