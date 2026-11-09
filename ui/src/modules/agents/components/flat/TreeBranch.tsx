/**
 * TreeBranch — one child of the "Can call" tree, in the Library ledger's
 * geometry (`CatalogLedgerRows`), with its trunk segment and elbow drawn as
 * REAL elements (not `::before`/`::after`) so the list entrance can animate
 * them: the trunk draws down, the row rises in, the elbow wipes out to meet it
 * (`lib/apiViewMotion`). Outside an animating parent they render at rest.
 *
 * Geometry: the trunk sits at x 11,
 * just left of the card avatar's centre; each elbow is 17px wide with an 8px
 * radius and runs from the branch's top into its child's centre; the child
 * starts 6px past the elbow (`pl-[34px]` = 11 + 17 + 6), so a line never
 * touches a row. The trunk carries on only past children with a sibling below,
 * or past a branch that `continues` into the list after it (the held calls
 * pinned with the agent card, above the API rows).
 * Strokes are a true 1.5px: Chromium snaps border widths (and a bar's layout
 * width) to whole pixels, so the trunk is a 3px bar scaled to half its width
 * from the left, and the elbow is drawn at twice its size (3px border, 16px
 * radius) and scaled to half from its top-left. Both start at x 11, so the
 * elbow's left stroke overlays the trunk exactly.
 *
 * The scale-to-1.5px lives on an INNER element and the motion on an outer
 * one, so the animation never fights the stroke geometry.
 */
import type { ReactNode } from 'react';
import { motion } from 'framer-motion';
import { cn } from '@/shared/lib/utils';
import {
	TREE_ELBOW_VARIANTS,
	TREE_ROW_VARIANTS,
	TREE_TRUNK_VARIANTS,
	type LensItem,
} from '@/modules/agents/lib/apiViewMotion';

/**
 * Where the elbow lands: a 64px row's centre (10px + 32px) or the 32px Add
 * APIs button's centre (10px + 16px), which lines up with the rows' avatars.
 */
const ELBOW = {
	row: { box: 'h-[42px]', drawn: 'h-[84px]' },
	button: { box: 'h-[26px]', drawn: 'h-[52px]' },
} as const;

interface TreeBranchProps {
	/** What the elbow lands on. */
	lands: keyof typeof ELBOW;
	/** This branch's place in the tree, for the entrance stagger. */
	item: LensItem;
	/** A list item (the default), or a lone branch outside a list. */
	as?: 'li' | 'div';
	/**
	 * The trunk runs on past this branch even with no sibling after it, and
	 * through the 3px gap the next list leaves before its first elbow, so the
	 * two read as one tree.
	 */
	continues?: boolean;
	className?: string;
	children: ReactNode;
}

export function TreeBranch({
	lands,
	item,
	as: Branch = 'li',
	continues = false,
	className,
	children,
}: TreeBranchProps) {
	const elbow = ELBOW[lands];
	return (
		<Branch
			data-testid="tree-branch"
			className={cn('group/branch relative pt-2.5 pl-[34px]', className)}
		>
			{/* The trunk segment past this branch — only when a sibling follows,
			    or the tree carries on below. */}
			<motion.span
				aria-hidden="true"
				data-testid="tree-trunk"
				custom={item}
				variants={TREE_TRUNK_VARIANTS}
				className={cn(
					'pointer-events-none absolute top-0 left-[11px] w-[3px] origin-top',
					continues ? '-bottom-[3px]' : 'bottom-0 group-last/branch:hidden',
				)}
			>
				<span className="bg-ledger-tree block h-full w-full origin-left scale-x-50" />
			</motion.span>
			<motion.span
				aria-hidden="true"
				data-testid="tree-elbow"
				custom={item}
				variants={TREE_ELBOW_VARIANTS}
				className={cn('pointer-events-none absolute top-0 left-[11px] w-[17px]', elbow.box)}
			>
				<span
					className={cn(
						'border-ledger-tree block w-[34px] origin-top-left scale-50 rounded-bl-[16px] border-b-[3px] border-l-[3px]',
						elbow.drawn,
					)}
				/>
			</motion.span>
			<motion.div custom={item} variants={TREE_ROW_VARIANTS}>
				{children}
			</motion.div>
		</Branch>
	);
}
