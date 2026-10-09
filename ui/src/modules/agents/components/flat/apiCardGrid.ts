/**
 * The "Can call" cards grid's shared geometry, used by `ApiCard`, the grid's
 * dashed "+ Add API" tile and the grid itself, so every cell is exactly the same
 * size and the status rows line up across the grid.
 */

/**
 * ONE fixed height for every card and the add tile (a fixed `h-`, never
 * `min-h-`): 10px padding + a 28px name row + a 16px credential row + an 18px
 * status row, with the 20px left over split evenly between the rows.
 */
export const API_CARD_HEIGHT = 'h-[92px]';

/**
 * The dense grid's columns: 2-up on phones (≤640px), 3 between, ≥4 from 1100px
 * and 5 from 1680px, ~12px apart. Literals, so Tailwind sees them.
 */
export const API_CARD_GRID =
	'grid grid-cols-2 gap-3 min-[641px]:grid-cols-3 min-[1100px]:grid-cols-4 min-[1680px]:grid-cols-5';
