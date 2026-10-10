/**
 * ApiViewToggle — the list⇄cards lens switcher: a two-option radiogroup of icon
 * buttons with roving arrow-key selection, named by aria-label (no tooltip).
 */
import { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import {
	act,
	renderWithProviders,
	screen,
	within,
	fireEvent,
	checkA11y,
} from '@/__tests__/test-utils';
import { TOOLTIP_DELAY_MS } from '@/shared/ui';
import { ApiViewToggle } from '@/modules/agents/components/flat/ApiViewToggle';
import type { ApiView } from '@/modules/agents/lib/apiView';

function Harness({
	initial = 'list',
	onChange,
}: {
	initial?: ApiView;
	onChange?: (view: ApiView) => void;
}) {
	const [view, setView] = useState<ApiView>(initial);
	return (
		<ApiViewToggle
			value={view}
			onChange={(next) => {
				setView(next);
				onChange?.(next);
			}}
		/>
	);
}

const group = () => screen.getByTestId('api-view-toggle');
const listRadio = () => screen.getByRole('radio', { name: 'List view' });
const cardsRadio = () => screen.getByRole('radio', { name: 'Cards view' });

describe('ApiViewToggle', () => {
	it('is a radiogroup of two radios, the active one checked and tabbable', async () => {
		const { container } = renderWithProviders(<Harness initial="list" />);

		expect(group()).toHaveAttribute('role', 'radiogroup');
		const radios = within(group()).getAllByRole('radio');
		expect(radios).toHaveLength(2);
		expect(listRadio()).toHaveAttribute('aria-checked', 'true');
		expect(cardsRadio()).toHaveAttribute('aria-checked', 'false');
		// Roving tabindex: only the checked option is in the tab order.
		expect(listRadio()).toHaveAttribute('tabindex', '0');
		expect(cardsRadio()).toHaveAttribute('tabindex', '-1');
		await checkA11y(container);
	});

	it('switches the selection on click', () => {
		const onChange = vi.fn();
		renderWithProviders(<Harness initial="list" onChange={onChange} />);

		fireEvent.click(cardsRadio());
		expect(onChange).toHaveBeenCalledWith('cards');
		expect(cardsRadio()).toHaveAttribute('aria-checked', 'true');
		expect(listRadio()).toHaveAttribute('aria-checked', 'false');
	});

	it('moves AND selects with the arrow keys (radiogroup pattern)', () => {
		const onChange = vi.fn();
		renderWithProviders(<Harness initial="list" onChange={onChange} />);

		listRadio().focus();
		fireEvent.keyDown(listRadio(), { key: 'ArrowRight' });
		expect(onChange).toHaveBeenLastCalledWith('cards');
		expect(cardsRadio()).toHaveAttribute('aria-checked', 'true');

		fireEvent.keyDown(cardsRadio(), { key: 'ArrowLeft' });
		expect(onChange).toHaveBeenLastCalledWith('list');

		fireEvent.keyDown(listRadio(), { key: 'End' });
		expect(onChange).toHaveBeenLastCalledWith('cards');
		fireEvent.keyDown(cardsRadio(), { key: 'Home' });
		expect(onChange).toHaveBeenLastCalledWith('list');
	});

	it('names each option for assistive tech by aria-label, with no tooltip or title', async () => {
		renderWithProviders(<Harness initial="cards" />);
		expect(listRadio()).toHaveAttribute('aria-label', 'List view');
		expect(cardsRadio()).toHaveAttribute('aria-label', 'Cards view');
		expect(listRadio()).toHaveAccessibleName('List view');
		expect(cardsRadio()).toHaveAccessibleName('Cards view');
		for (const radio of [listRadio(), cardsRadio()]) {
			expect(radio).not.toHaveAttribute('title');
			expect(radio).not.toHaveAttribute('aria-describedby');
		}
		// Hover and focus reveal nothing — the glyphs carry no tooltip.
		vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
		try {
			fireEvent.mouseEnter(listRadio());
			fireEvent.focus(cardsRadio());
			act(() => void vi.advanceTimersByTime(TOOLTIP_DELAY_MS + 100));
			expect(screen.queryByRole('tooltip')).toBeNull();
		} finally {
			vi.useRealTimers();
		}
	});
});
