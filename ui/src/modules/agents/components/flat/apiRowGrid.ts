/**
 * The "Can call" row's column grid, shared by the resting row (`ApiRow`) and
 * the layer it grows (`ApiRowReveal`) so their edges line up exactly: both
 * sit in the same row box, with the same inset and the same template.
 *
 * Columns (lg+): logo · name/host · auth/credential · metric + status ·
 * controls (Manage access, then the accordion chevron — two 28px icon
 * buttons, 4px apart). The middle three are equal thirds — the reveal's three
 * columns sit under them — and the third splits into the 7-day metric and the
 * status (`API_ROW_METRIC_STATUS_LG`).
 */

/** The row's horizontal inset — the rest grid and the reveal both use it. */
export const API_ROW_INSET = 'pr-3 pl-3.5';

/** The column gap between the row's cells. */
export const API_ROW_GAP = 'gap-x-4';

/** The template below lg: logo · text · controls. A literal, so Tailwind sees it. */
export const API_ROW_COLUMNS = 'grid-cols-[36px_minmax(0,1fr)_60px]';

/** The lg+ column template. A literal, so Tailwind sees it. */
export const API_ROW_COLUMNS_LG = 'lg:grid-cols-[36px_repeat(3,minmax(0,1fr))_60px]';

/** The third column's own split at lg+: the metric, then the status marker. */
export const API_ROW_METRIC_STATUS_LG = 'lg:grid-cols-[minmax(0,1fr)_minmax(0,164px)]';
