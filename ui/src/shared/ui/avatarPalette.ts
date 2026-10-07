/**
 * Pastel identity palette — one source for every initials tile in the app
 * (API/vendor marks, agent badges, Monitor's busiest-API tiles).
 *
 * Eight hue/saturation pairs: the tile is the hue at 80% lightness, the
 * initials the same hue at 24% (saturation +10, capped at 60). Every pair
 * clears WCAG AA for the initials (≥ 5.4:1), and the colour never carries
 * meaning on its own — the name is always printed beside the tile.
 *
 * The HSL triplets live in `index.css` (`--avatar-{i}-bg/fg`); this module
 * only picks an index, deterministically, from a stable seed.
 */
import type { CSSProperties } from 'react';

/** [hue, saturation] per tone — mirrors the `--avatar-*` tokens (tests read it). */
export const AVATAR_TONES: readonly (readonly [number, number])[] = [
	[200, 52], // sky
	[165, 40], // mint
	[140, 34], // sage
	[45, 55], // sand
	[25, 58], // peach
	[355, 45], // rose
	[300, 28], // orchid
	[250, 38], // iris
];

export const AVATAR_TONE_COUNT = AVATAR_TONES.length;

/** Stable string hash (djb2) — the same seed always lands on the same tone. */
function hashSeed(input: string): number {
	let hash = 5381;
	for (let i = 0; i < input.length; i += 1) {
		hash = (hash * 33) ^ input.charCodeAt(i);
	}
	return Math.abs(hash);
}

/** Tone index (0…7) for a seed (vendor key, agent id, …). */
export function avatarToneIndex(seed: string): number {
	return hashSeed(seed) % AVATAR_TONE_COUNT;
}

/** Inline colours for a tone (background tile + same-hue dark initials). */
export function avatarToneStyle(index: number): CSSProperties {
	return {
		backgroundColor: `hsl(var(--avatar-${index}-bg))`,
		color: `hsl(var(--avatar-${index}-fg))`,
	};
}

/**
 * Chart tone — the same hue a touch deeper and richer than the 80% tile
 * (saturation +12, capped at 62; lightness 72%), so a large bar / bubble fill
 * reads as colour on the dark surface instead of a washed-out pastel. The
 * tile's dark initials still clear AA on it (≥ 4.9:1), which is what lets a
 * bubble carry its initials directly. Computed here only (charts fill SVG
 * directly), so there is no CSS token for it.
 */
export const AVATAR_CHART_LIGHTNESS = 72;

function chartSaturation(s: number): number {
	return Math.min(s + 12, 62);
}

export interface AvatarToneColors {
	/** Tile background (80%). */
	bg: string;
	/** Initials (same hue, 24%). */
	fg: string;
	/** Chart fill (same hue, deeper — bars, bubbles, sparklines). */
	chart: string;
}

/** The tile/initials/chart triple as plain HSL strings (for SVG fills and tests). */
export function avatarToneColors(index: number): AvatarToneColors {
	const [h, s] = AVATAR_TONES[index % AVATAR_TONE_COUNT];
	return {
		bg: `hsl(${h} ${s}% 80%)`,
		fg: `hsl(${h} ${Math.min(s + 10, 60)}% 24%)`,
		chart: `hsl(${h} ${chartSaturation(s)}% ${AVATAR_CHART_LIGHTNESS}%)`,
	};
}

/**
 * Neutral tone for buckets with no identity ("Other", "Unattributed"): a grey
 * tile and a desaturated mid-grey chart fill (darker than every pastel, so the
 * remainder recedes and never impersonates a real entity), with dark
 * initials that clear AA on both (≥ 5.5:1).
 * `bg` / `fg` mirror `--avatar-neutral-{bg,fg}`.
 */
export const AVATAR_NEUTRAL: AvatarToneColors = {
	bg: 'hsl(192 10% 72%)',
	fg: 'hsl(192 20% 12%)',
	chart: 'hsl(192 8% 58%)',
};
