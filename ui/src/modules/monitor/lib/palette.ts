/**
 * Shared palette + initials helpers for the Overview charts (bubble chart,
 * breakdown table, HealthStrip avatar cluster).
 *
 * jentic-mini resolves per-vendor brand colours/icons via its `vendor-icons`
 * registry; jentic-one has no such registry, so every lens gets a stable
 * index-based palette with initials tiles (the same approach the module's
 * Breakdown already used). Palettes are lifted verbatim from jentic-mini so
 * the look carries over.
 */

export const API_PALETTE = [
	'#6366f1',
	'#0ea5e9',
	'#8b5cf6',
	'#10b981',
	'#f59e0b',
	'#ef4444',
	'#14b8a6',
	'#ec4899',
];

export const TOOLKIT_PALETTE = [
	'#6366f1',
	'#8b5cf6',
	'#0ea5e9',
	'#14b8a6',
	'#f59e0b',
	'#ef4444',
	'#ec4899',
	'#10b981',
];

export const AGENT_PALETTE = ['#0891b2', '#7c3aed', '#db2777', '#16a34a', '#ea580c', '#475569'];

export type UsageLens = 'apis' | 'toolkits' | 'agents';

export function lensPalette(lens: UsageLens): string[] {
	if (lens === 'agents') return AGENT_PALETTE;
	if (lens === 'toolkits') return TOOLKIT_PALETTE;
	return API_PALETTE;
}

/** "stripe-api" → "S", "Billing Agent" → "BA". Mirrors jentic-mini's helper. */
export function getInitials(name: string): string {
	const words = name
		.replace(/api/gi, '')
		.trim()
		.split(/[\s\-_/.]+/)
		.filter(Boolean);
	return ((words[0]?.[0] ?? '?') + (words[1]?.[0] ?? '')).toUpperCase();
}

/**
 * Lighten a hex colour for the bubble's success ring (mini used a per-vendor
 * `ring` colour; we derive one from the fill instead of a second palette).
 */
export function ringColor(hex: string): string {
	const m = /^#([0-9a-f]{6})$/i.exec(hex);
	if (!m) return hex;
	const num = parseInt(m[1], 16);
	const lift = (c: number) => Math.min(255, Math.round(c + (255 - c) * 0.35));
	const r = lift((num >> 16) & 0xff);
	const g = lift((num >> 8) & 0xff);
	const b = lift(num & 0xff);
	return `#${((r << 16) | (g << 8) | b).toString(16).padStart(6, '0')}`;
}
