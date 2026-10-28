/**
 * The "waiting for you" accent for held calls, shared by the waiting card on
 * an agent, the strip tabs, the bell, the rail's Inbox and the attention
 * toast. The colours are theme tokens (`--attention-*` in `index.css`): on the
 * dark theme the surfaces stay neutral and amber rides on the glyph, the count
 * and a hairline; the light theme adds a faint warm fill.
 */

/** The square glyph tile beside a held call (the card head, bell rows, the toast). */
export const ATTENTION_TILE_CLASS = 'bg-attention-tile text-warning';

/** A count chip (the Inbox tab, an agent's strip tab); its glyph takes `text-warning`. */
export const ATTENTION_CHIP_CLASS = 'bg-attention-chip text-attention-chip-foreground';

/** The solid count pill on the collapsed strip (the bell's own badge style). */
export const ATTENTION_COUNT_CLASS = 'bg-warning text-background';

/** The waiting card's head: no fill on the dark theme, a faint warm one on the light. */
export const ATTENTION_FILL_CLASS = 'bg-attention-fill';

/** A request waiting on the viewer in a list: a neutral row on dark, a faint warm one on light. */
export const ATTENTION_ROW_CLASS = 'bg-attention-row';

/** The waiting card's hairline ring, drawn over its content by an `::after`. */
export const ATTENTION_RING_CLASS = 'after:shadow-[inset_0_0_0_1px_var(--color-attention-ring)]';
