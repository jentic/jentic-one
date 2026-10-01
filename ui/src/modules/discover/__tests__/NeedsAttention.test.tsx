import { describe, it, expect } from 'vitest';
import { renderWithProviders, screen, userEvent } from '@/__tests__/test-utils';
import {
	ATTENTION_EXPANDED_KEY,
	NeedsAttention,
} from '@/modules/discover/components/NeedsAttention';
import type { AttentionEntry } from '@/modules/discover/api';
import { makeDigestRow } from '@/modules/discover/__tests__/digestFixtures';

const [a, b, c] = ['Alpha', 'Beta', 'Gamma'].map((t) => makeDigestRow(t));

// In the digest's ATTENTION_ORDER (by impact on agents); the component keeps it.
const ALL: AttentionEntry[] = [
	{ id: 'failures', label: 'failed calls in the last 7 days', rows: [a, b], tab: 'overview' },
	{ id: 'credentials', label: 'no credential', rows: [b, c], tab: 'overview' },
	{ id: 'drafts', label: 'draft only', rows: [c], tab: 'versions' },
	{ id: 'updates', label: 'upstream update available', rows: [a], tab: 'overview' },
];

function renderAttention(attention: AttentionEntry[]) {
	return renderWithProviders(<NeedsAttention attention={attention} />);
}

const toggle = () => screen.getByTestId('workspace-panel-attention-toggle');
const items = () =>
	screen.queryAllByTestId(/^attention-(updates|overlays|failures|credentials|drafts)$/);

describe('NeedsAttention — ≤ 2 items: nothing to collapse', () => {
	it.each([1, 2])('%i item(s): plain heading, no toggle, all items shown', (n) => {
		renderAttention(ALL.slice(0, n));
		expect(
			screen.getByRole('heading', { level: 3, name: `Needs attention · ${n}` }),
		).toBeInTheDocument();
		expect(screen.queryByTestId('workspace-panel-attention-toggle')).not.toBeInTheDocument();
		expect(screen.queryByRole('button')).not.toBeInTheDocument();
		expect(items()).toHaveLength(n);
		expect(screen.getByTestId('workspace-panel-attention')).toHaveAttribute(
			'data-expanded',
			'true',
		);
	});

	it.each(['0', '1'])('ignores a stored preference (%s) and leaves it untouched', (v) => {
		window.localStorage.setItem(ATTENTION_EXPANDED_KEY, v);
		renderAttention(ALL.slice(0, 2));
		expect(screen.queryByTestId('workspace-panel-attention-toggle')).not.toBeInTheDocument();
		expect(items()).toHaveLength(2);
		expect(window.localStorage.getItem(ATTENTION_EXPANDED_KEY)).toBe(v);
	});
});

describe('NeedsAttention — > 2 items: collapsible', () => {
	it('is collapsed by default, header is the toggle inside the heading', () => {
		renderAttention(ALL);
		expect(toggle()).toHaveAttribute('aria-expanded', 'false');
		expect(toggle()).toHaveAttribute('aria-controls');
		expect(toggle()).toHaveTextContent('Needs attention · 4');
		expect(
			screen.getByRole('heading', { level: 3, name: /Needs attention · 4/ }),
		).toContainElement(toggle());
		expect(items()).toHaveLength(2);
	});

	it('persists the user’s toggle, and the stored choice wins over the default', async () => {
		const user = userEvent.setup();
		const { unmount } = renderAttention(ALL);
		await user.click(toggle());
		expect(toggle()).toHaveAttribute('aria-expanded', 'true');
		expect(items()).toHaveLength(4);
		expect(window.localStorage.getItem(ATTENTION_EXPANDED_KEY)).toBe('1');
		unmount();

		// Next visit: stored "expanded" beats the collapsed default.
		renderAttention(ALL);
		expect(toggle()).toHaveAttribute('aria-expanded', 'true');
	});

	it('collapsing again is remembered too', async () => {
		const user = userEvent.setup();
		window.localStorage.setItem(ATTENTION_EXPANDED_KEY, '1');
		renderAttention(ALL);
		await user.click(toggle());
		expect(toggle()).toHaveAttribute('aria-expanded', 'false');
		expect(items()).toHaveLength(2);
		expect(window.localStorage.getItem(ATTENTION_EXPANDED_KEY)).toBe('0');
	});
});

describe('NeedsAttention — collapsed: top 2 + Show all', () => {
	it('shows the first 2 items in full, then "Show all (N)" which expands', async () => {
		const user = userEvent.setup();
		renderAttention(ALL);
		expect(items().map((el) => el.dataset.testid)).toEqual([
			'attention-failures',
			'attention-credentials',
		]);
		const showAll = screen.getByRole('button', { name: 'Show all (4)' });
		await user.click(showAll);
		expect(toggle()).toHaveAttribute('aria-expanded', 'true');
		expect(items()).toHaveLength(4);
		expect(screen.queryByTestId('attention-show-all')).not.toBeInTheDocument();
	});
});
