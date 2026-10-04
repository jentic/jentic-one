/**
 * Monitor's per-entity colours — derived from the shared pastel avatar
 * palette (`@/shared/ui/avatarPalette`), never a palette of its own.
 *
 * An API / agent's chart colour is its avatar hue: the same seed
 * (`entitySeed`) feeds `avatarToneIndex`, exactly as `VendorIcon` /
 * `AgentBadge` do elsewhere in the app, so the bar segment, bubble, sparkline,
 * legend / tooltip mark and breakdown tile of one entity all share one hue.
 *
 * The one chart-specific twist: series inside one chart must stay
 * distinguishable. Eight hues and a hash means two entities can land on the
 * same tone, so {@link assignChartTones} walks the rows busiest-first and,
 * when an entity's own tone is already taken in that chart, moves it to the
 * next free palette tone (index + 1, + 2, … mod 8). The busiest entity always
 * keeps its true hue; the result is deterministic for a given row set. With
 * more than eight entities hues have to repeat, so the overflow keeps its
 * natural tone. Every surface of the chart (legend, tooltip, breakdown) reads
 * the same assignment, so the fallback is mirrored everywhere it shows.
 *
 * Buckets with no identity ("Other", "Unattributed") take the neutral grey.
 */
import {
	AVATAR_NEUTRAL,
	AVATAR_TONE_COUNT,
	agentInitials,
	avatarToneColors,
	avatarToneIndex,
} from '@/shared/ui';
import { UNATTRIBUTED_ID, UNATTRIBUTED_LABEL } from '@/modules/monitor/lib/usage';

export type UsageLens = 'apis' | 'agents';

/** Synthetic id for the per-bar remainder outside the top rows. */
export const OTHER_KEY = '__other__';

/**
 * The avatar seed for a usage row — the same key `VendorIcon` / `AgentBadge`
 * hash on every other Monitor surface:
 *
 *   apis   `vendor/name`        → `vendor` (as the API-calls log seeds `api.vendor`)
 *   agents `actor_type/actor_id` → `actor_id` (as the Agents pages seed `agent.id`)
 *
 * Returns null for buckets with no identity (Unattributed / unknown).
 */
export function entitySeed(lens: UsageLens, id: string): string | null {
	if (!id || id === UNATTRIBUTED_ID || id === OTHER_KEY) return null;
	const slash = id.indexOf('/');
	if (lens === 'apis') {
		const vendor = slash < 0 ? id : id.slice(0, slash);
		return vendor && vendor !== 'unknown' ? vendor : null;
	}
	if (slash < 0) return id;
	return id.slice(slash + 1) || null;
}

/** Resolved colours for one entity in one chart. */
export interface EntityTone {
	/** Palette index used in this chart (null = neutral). */
	tone: number | null;
	/** The entity's own avatar tone (what `VendorIcon` / `AgentBadge` draw). */
	avatarTone: number | null;
	/** Seed passed to `VendorIcon` (`vendor`) / `AgentBadge` (`id`). */
	seed: string | null;
	/** Chart fill (bars, bubbles, sparklines, volume bars). */
	fill: string;
	/** Tile colour (bubble success ring, swatches). */
	tile: string;
	/** Dark initials ink — AA on both `fill` and `tile`. */
	ink: string;
}

function toneColors(tone: number | null) {
	return tone == null ? AVATAR_NEUTRAL : avatarToneColors(tone);
}

function makeTone(tone: number | null, avatarTone: number | null, seed: string | null): EntityTone {
	const c = toneColors(tone);
	return { tone, avatarTone, seed, fill: c.chart, tile: c.bg, ink: c.fg };
}

/** Neutral tone for the "Other" remainder. */
export const OTHER_TONE: EntityTone = makeTone(null, null, null);

/**
 * Chart tones for one chart's rows (pass them busiest-first — the order every
 * usage row list already has). See the module doc for the collision rule.
 */
export function assignChartTones(
	lens: UsageLens,
	rows: readonly { id: string; label?: string }[],
): Map<string, EntityTone> {
	const used = new Set<number>();
	const out = new Map<string, EntityTone>();
	for (const row of rows) {
		const seed = row.label === UNATTRIBUTED_LABEL ? null : entitySeed(lens, row.id);
		if (seed == null) {
			out.set(row.id, makeTone(null, null, null));
			continue;
		}
		const natural = avatarToneIndex(seed);
		let tone = natural;
		if (used.size < AVATAR_TONE_COUNT) {
			for (let step = 0; step < AVATAR_TONE_COUNT; step += 1) {
				const candidate = (natural + step) % AVATAR_TONE_COUNT;
				if (!used.has(candidate)) {
					tone = candidate;
					break;
				}
			}
		}
		used.add(tone);
		out.set(row.id, makeTone(tone, natural, seed));
	}
	return out;
}

/** Lookup with the neutral fallback (ids outside the assigned rows). */
export function toneFor(tones: Map<string, EntityTone>, id: string): EntityTone {
	return tones.get(id) ?? OTHER_TONE;
}

/**
 * The initials an entity's avatar shows, so a bubble reads like its tile:
 * APIs follow `VendorIcon` (first two alphanumerics — "stripe-api" → "ST"),
 * agents follow `AgentBadge` ({@link agentInitials}). Never blank.
 */
export function entityInitials(lens: UsageLens, label: string): string {
	if (lens === 'agents') return agentInitials(label) || '?';
	return (
		label
			.replace(/[^a-z0-9]/gi, '')
			.slice(0, 2)
			.toUpperCase() || '??'
	);
}
