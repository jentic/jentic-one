import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { AgentBadge } from '@/shared/ui/AgentBadge';
import { VendorIcon } from '@/shared/ui/VendorIcon';
import {
	AVATAR_CHART_LIGHTNESS,
	AVATAR_NEUTRAL,
	AVATAR_TONES,
	AVATAR_TONE_COUNT,
	avatarToneColors,
	avatarToneIndex,
} from '@/shared/ui/avatarPalette';

function hslToRgb(h: number, s: number, l: number): [number, number, number] {
	const sn = s / 100;
	const ln = l / 100;
	const k = (n: number) => (n + h / 30) % 12;
	const a = sn * Math.min(ln, 1 - ln);
	const f = (n: number) => ln - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
	return [f(0) * 255, f(8) * 255, f(4) * 255];
}

function luminance([r, g, b]: [number, number, number]) {
	const c = [r, g, b].map((v) => {
		const x = v / 255;
		return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
	});
	return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}

function contrast(a: [number, number, number], b: [number, number, number]) {
	const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
	return (hi + 0.05) / (lo + 0.05);
}

describe('avatar palette', () => {
	it('is deterministic per seed and stays in range', () => {
		for (const seed of ['stripe.com', 'googleapis.com', 'agnt_1', '']) {
			const i = avatarToneIndex(seed);
			expect(i).toBe(avatarToneIndex(seed));
			expect(i).toBeGreaterThanOrEqual(0);
			expect(i).toBeLessThan(AVATAR_TONE_COUNT);
		}
	});

	it('spreads seeds across the palette', () => {
		const seen = new Set(
			Array.from({ length: 64 }, (_, n) => avatarToneIndex(`vendor-${n}.com`)),
		);
		expect(seen.size).toBeGreaterThanOrEqual(6);
	});

	it('every tile/initials pair clears WCAG AA (≥ 4.5:1)', () => {
		AVATAR_TONES.forEach(([h, s]) => {
			const bg = hslToRgb(h, s, 80);
			const fg = hslToRgb(h, Math.min(s + 10, 60), 24);
			expect(contrast(bg, fg)).toBeGreaterThanOrEqual(4.5);
		});
	});

	it('the CSS tokens match the palette table', () => {
		const root = getComputedStyle(document.documentElement);
		AVATAR_TONES.forEach(([h, s], i) => {
			expect(root.getPropertyValue(`--avatar-${i}-bg`).trim()).toBe(`${h} ${s}% 80%`);
			expect(avatarToneColors(i).bg).toBe(
				`hsl(${root.getPropertyValue(`--avatar-${i}-bg`).trim()})`,
			);
			expect(avatarToneColors(i).fg).toBe(
				`hsl(${root.getPropertyValue(`--avatar-${i}-fg`).trim()})`,
			);
		});
		// Chart tones are JS-only (SVG fills); only the tile/initials have tokens.
		for (const k of ['bg', 'fg'] as const) {
			expect(AVATAR_NEUTRAL[k]).toBe(
				`hsl(${root.getPropertyValue(`--avatar-neutral-${k}`).trim()})`,
			);
		}
	});

	it('chart tones keep the hue and still carry the dark initials (≥ 4.5:1)', () => {
		AVATAR_TONES.forEach(([h, s], i) => {
			const m = /^hsl\((\d+) (\d+)% (\d+)%\)$/.exec(avatarToneColors(i).chart)!;
			expect(Number(m[1])).toBe(h);
			// Deeper than the 80% tile so a bar fill doesn't wash out.
			expect(Number(m[3])).toBe(AVATAR_CHART_LIGHTNESS);
			expect(AVATAR_CHART_LIGHTNESS).toBeLessThan(80);
			const chart = hslToRgb(h, Number(m[2]), Number(m[3]));
			const fg = hslToRgb(h, Math.min(s + 10, 60), 24);
			expect(contrast(chart, fg)).toBeGreaterThanOrEqual(4.5);
		});
		const parse = (c: string) => {
			const [h, sat, l] = /^hsl\((\d+) (\d+)% (\d+)%\)$/.exec(c)!.slice(1).map(Number);
			return hslToRgb(h, sat, l);
		};
		// Neutral "Other / Unattributed" ink is AA on its tile and its chart fill.
		const ink = parse(AVATAR_NEUTRAL.fg);
		expect(contrast(parse(AVATAR_NEUTRAL.bg), ink)).toBeGreaterThanOrEqual(4.5);
		expect(contrast(parse(AVATAR_NEUTRAL.chart), ink)).toBeGreaterThanOrEqual(4.5);
	});

	it('VendorIcon and AgentBadge share the palette for the same seed', () => {
		const { container: v } = render(<VendorIcon name="x" vendor="seed-1" />);
		const { container: a } = render(<AgentBadge id="seed-1" name="x" />);
		const tone = String(avatarToneIndex('seed-1'));
		expect(v.firstElementChild?.getAttribute('data-tone')).toBe(tone);
		expect(a.firstElementChild?.getAttribute('data-tone')).toBe(tone);
	});
});
