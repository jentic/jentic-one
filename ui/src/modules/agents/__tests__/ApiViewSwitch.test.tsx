/**
 * ApiViewSwitch + TreeBranch — the list⇄cards lens motion: cards fade up
 * staggered; the list grows its tree (trunk draws down, rows rise, elbows wipe
 * out); the container height tweens between layouts and settles back to
 * `auto`; reduced motion swaps instantly. Plus the stagger caps and the tree's
 * at-rest geometry.
 */
import { useState } from 'react';
import { describe, it, expect } from 'vitest';
import { MotionConfig, motion } from 'framer-motion';
import { renderWithProviders, screen, fireEvent, waitFor } from '@/__tests__/test-utils';
import { ApiViewSwitch } from '@/modules/agents/components/flat/ApiViewSwitch';
import { TreeBranch } from '@/modules/agents/components/flat/TreeBranch';
import {
	LENS_CARD_VARIANTS,
	lensStaggerStep,
	treeStaggerStep,
} from '@/modules/agents/lib/apiViewMotion';
import type { ApiView } from '@/modules/agents/lib/apiView';

const N = 14;

function Harness({
	reducedMotion,
	initial = 'cards',
}: {
	reducedMotion: 'always' | 'never';
	initial?: ApiView;
}) {
	const [view, setView] = useState<ApiView>(initial);
	return (
		<MotionConfig reducedMotion={reducedMotion}>
			<button type="button" onClick={() => setView(view === 'list' ? 'cards' : 'list')}>
				flip
			</button>
			<ApiViewSwitch view={view}>
				{view === 'cards' ? (
					<ul data-testid="layout-cards">
						{Array.from({ length: 4 }, (_, i) => (
							<motion.li
								key={i}
								custom={{ i, n: 4 }}
								variants={LENS_CARD_VARIANTS}
								style={{ height: 40 }}
							>
								card {i}
							</motion.li>
						))}
					</ul>
				) : (
					<ul data-testid="layout-list" className="pt-[3px] pl-[31px]">
						{Array.from({ length: N }, (_, i) => (
							<TreeBranch key={i} lands="row" item={{ i, n: N }}>
								<div style={{ height: 64 }}>row {i}</div>
							</TreeBranch>
						))}
					</ul>
				)}
			</ApiViewSwitch>
		</MotionConfig>
	);
}

const switchEl = () => screen.getByTestId('api-view-switch');
const flip = () => fireEvent.click(screen.getByRole('button', { name: 'flip' }));

describe('lens stagger caps', () => {
	it('caps the card stagger so a long grid starts its last card ≤150ms in', () => {
		expect(lensStaggerStep(1)).toBe(0);
		expect(lensStaggerStep(4)).toBeCloseTo(0.024);
		for (const n of [14, 30, 100])
			expect(lensStaggerStep(n) * (n - 1)).toBeLessThanOrEqual(0.15);
	});

	it('staggers rows ~35ms apart, capping where the last branch starts', () => {
		expect(treeStaggerStep(4)).toBeCloseTo(0.035);
		for (const n of [14, 30, 100])
			expect(treeStaggerStep(n) * (n - 1)).toBeLessThanOrEqual(0.3);
	});
});

describe('ApiViewSwitch', () => {
	it('does not animate the first render', () => {
		renderWithProviders(<Harness reducedMotion="never" />);
		expect(screen.getByTestId('layout-cards')).toBeInTheDocument();
		expect(switchEl()).not.toHaveAttribute('data-switching');
	});

	it('pins and tweens the height during a switch, then settles back to auto', async () => {
		renderWithProviders(<Harness reducedMotion="never" />);
		flip();
		// The outgoing layout holds while it fades; the height is pinned.
		expect(switchEl()).toHaveAttribute('data-switching', 'true');
		expect(switchEl().style.overflow).toBe('hidden');
		await screen.findByTestId('layout-list');
		await waitFor(
			() => {
				expect(switchEl()).not.toHaveAttribute('data-switching');
				expect(switchEl().style.height).toBe('auto');
			},
			{ timeout: 3000 },
		);
		expect(switchEl().style.overflow).toBe('');
		// 3px + 14 branches × (10px + 64px row).
		expect(Math.round(switchEl().getBoundingClientRect().height)).toBe(3 + N * 74);
	});

	it('grows the tree on entering the list: trunk from scaleY 0, rows from below', async () => {
		renderWithProviders(<Harness reducedMotion="never" />);
		flip();
		await screen.findByTestId('layout-list');
		const trunks = screen.getAllByTestId('tree-trunk');
		const rows = screen
			.getAllByTestId('tree-branch')
			.map((b) => b.lastElementChild as HTMLElement);
		// Early in the entrance the LAST branch hasn't joined yet.
		expect(trunks[trunks.length - 2]!.style.transform).toMatch(/scaleY\(0\)|scaleY\(0\.0/);
		expect(Number(rows[rows.length - 1]!.style.opacity)).toBeLessThan(1);
		// Once settled, everything is at rest — the geometry is the static one.
		await waitFor(() => expect(switchEl()).not.toHaveAttribute('data-switching'), {
			timeout: 3000,
		});
		for (const t of trunks) expect(['', 'none']).toContain(t.style.transform);
		for (const r of rows) expect(r.style.opacity).toBe('1');
	});

	it('swaps instantly under reduced motion — no fade, no height tween', () => {
		renderWithProviders(<Harness reducedMotion="always" />);
		flip();
		// Same commit: the new layout is already there, fully drawn.
		expect(screen.getByTestId('layout-list')).toBeInTheDocument();
		expect(screen.queryByTestId('layout-cards')).toBeNull();
		expect(switchEl()).not.toHaveAttribute('data-switching');
		const elbow = screen.getAllByTestId('tree-elbow')[0]!;
		expect(getComputedStyle(elbow).opacity).toBe('1');
	});
});

describe('TreeBranch geometry (at rest)', () => {
	it('keeps the ledger geometry: trunk at x 11, 17×42 elbow, 34px inset, no trunk past the last', () => {
		renderWithProviders(
			<ul data-testid="tree" className="pt-[3px] pl-[31px]" style={{ width: 600 }}>
				{[0, 1].map((i) => (
					<TreeBranch key={i} lands={i === 0 ? 'row' : 'button'} item={{ i, n: 2 }}>
						<div data-testid={`child-${i}`} style={{ height: i === 0 ? 64 : 32 }} />
					</TreeBranch>
				))}
			</ul>,
		);
		const [first, last] = screen.getAllByTestId('tree-branch');
		const ul = screen.getByTestId('tree').getBoundingClientRect();
		const li = first!.getBoundingClientRect();
		// The first branch starts 3px under the parent.
		expect(li.top - ul.top).toBe(3);
		const [elbowRow, elbowBtn] = screen.getAllByTestId('tree-elbow');
		const e = elbowRow!.getBoundingClientRect();
		expect(e.left - li.left).toBe(11);
		expect(e.width).toBe(17);
		expect(e.height).toBe(42);
		expect(elbowBtn!.getBoundingClientRect().height).toBe(26);
		// The drawn elbow: 3px border, 16px radius, scaled to half.
		const drawn = getComputedStyle(elbowRow!.firstElementChild!);
		expect(drawn.borderLeftWidth).toBe('3px');
		expect(drawn.borderBottomLeftRadius).toBe('16px');
		expect(drawn.scale).toBe('0.5');
		// Child 6px past the elbow: 11 + 17 + 6 = 34.
		expect(screen.getByTestId('child-0').getBoundingClientRect().left - li.left).toBe(34);
		// Trunk only past a branch with a sibling below.
		const [trunkFirst, trunkLast] = screen.getAllByTestId('tree-trunk');
		expect(getComputedStyle(trunkFirst!).display).not.toBe('none');
		expect(trunkFirst!.getBoundingClientRect().height).toBe(li.height);
		expect(getComputedStyle(trunkLast!).display).toBe('none');
		// The last branch sits flush under the first: no gap between siblings.
		expect(last!.getBoundingClientRect().top).toBe(li.bottom);
	});
});
