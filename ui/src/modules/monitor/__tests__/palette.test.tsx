/**
 * Monitor's per-entity colours come from the shared pastel avatar palette:
 * an API / agent's chart series, legend key, tooltip key, bubble and
 * breakdown tile all share the hue its `VendorIcon` / `AgentBadge` wears,
 * with a deterministic per-chart fallback when two entities collide.
 */
import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { renderWithProviders } from '@/__tests__/test-utils';
import type { UsageResponse } from '@/modules/monitor/api';
import { UsageCharts } from '@/modules/monitor/components/UsageCharts';
import { UsageBreakdown } from '@/modules/monitor/components/UsageBreakdown';
import { assignChartTones, entitySeed } from '@/modules/monitor/lib/palette';
import { usageToEntityRows, type EntityUsageRow } from '@/modules/monitor/lib/usage';
import {
	AVATAR_NEUTRAL,
	AgentBadge,
	VendorIcon,
	avatarToneColors,
	avatarToneIndex,
} from '@/shared/ui';

function row(id: string, label: string, total: number): EntityUsageRow {
	return {
		id,
		label,
		totalExecutions: total,
		successRate: 100,
		avgLatencyMs: 100,
		trend: [total],
	};
}

function usageFor(rows: { key: string; total: number }[]): UsageResponse {
	const until = Math.floor(Date.now() / 1000);
	const since = until - 86_400;
	const total = rows.reduce((s, r) => s + r.total, 0);
	return {
		since,
		until,
		bucket_seconds: 86_400,
		group_by: 'api',
		stats: {
			total,
			success: total,
			failed: 0,
			pending: 0,
			avg_ms: 100,
			p50_ms: 90,
			p95_ms: 200,
			active_now: 0,
		},
		buckets: [{ ts: since, total, success: total, failed: 0, avg_ms: 100 }],
		top: rows.map((r) => ({
			key: r.key,
			label: r.key,
			total: r.total,
			success: r.total,
			failed: 0,
			avg_ms: 100,
			trend: [r.total],
		})),
	};
}

/** Resolve a CSS colour expression to the browser's computed rgb(). */
function computed(expr: string): string {
	const probe = document.createElement('span');
	probe.style.color = expr;
	document.body.appendChild(probe);
	const out = getComputedStyle(probe).color;
	probe.remove();
	return out;
}

describe('Monitor entity colours', () => {
	it('seeds avatars exactly as the rest of the app does', () => {
		expect(entitySeed('apis', 'stripe/stripe-api')).toBe('stripe');
		expect(entitySeed('agents', 'agent/agnt_active_1')).toBe('agnt_active_1');
		expect(entitySeed('agents', 'service_account/sva_1')).toBe('sva_1');
		expect(entitySeed('apis', 'unknown/unknown')).toBeNull();
		expect(entitySeed('agents', '__unattributed__')).toBeNull();
	});

	it("an entity's chart colour is its avatar hue (bars, legend, VendorIcon)", () => {
		// acme → tone 7, globex → tone 6: no collision.
		const usage = usageFor([
			{ key: 'acme/acme-api', total: 9 },
			{ key: 'globex/globex-api', total: 4 },
		]);
		const rows = usageToEntityRows(usage);
		const { container } = renderWithProviders(
			<UsageCharts usage={usage} apis={rows} agents={[]} />,
		);
		for (const [key, seed] of [
			['acme/acme-api', 'acme'],
			['globex/globex-api', 'globex'],
		] as const) {
			const tone = avatarToneIndex(seed);
			const bar = container.querySelector(`rect[data-key="${key}"]`);
			expect(bar?.getAttribute('fill')).toBe(avatarToneColors(tone).chart);

			const { container: icon } = render(<VendorIcon name="x" vendor={seed} />);
			expect(icon.firstElementChild?.getAttribute('data-tone')).toBe(String(tone));
		}
		const legendTones = [...container.querySelectorAll('[data-testid="entity-mark"]')].map(
			(el) => el.getAttribute('data-tone'),
		);
		expect(legendTones).toEqual([
			String(avatarToneIndex('acme')),
			String(avatarToneIndex('globex')),
		]);
	});

	it('agents use the AgentBadge hue of their actor id', () => {
		const tones = assignChartTones('agents', [row('agent/agnt_active_1', 'agnt_active_1', 5)]);
		const { container } = render(<AgentBadge id="agnt_active_1" name="a" />);
		expect(String(tones.get('agent/agnt_active_1')?.tone)).toBe(
			container.firstElementChild?.getAttribute('data-tone'),
		);
	});

	it('falls back to the next free hue on a collision — busiest keeps its own', () => {
		// initech and umbrella both hash to tone 1.
		expect(avatarToneIndex('initech')).toBe(avatarToneIndex('umbrella'));
		const rows = [
			row('initech/a', 'initech-api', 10),
			row('umbrella/b', 'umbrella-api', 5),
			row('__unattributed__', 'Unattributed', 1),
		];
		const tones = assignChartTones('apis', rows);
		const natural = avatarToneIndex('initech');
		expect(tones.get('initech/a')?.tone).toBe(natural);
		expect(tones.get('umbrella/b')?.tone).toBe((natural + 1) % 8);
		expect(tones.get('umbrella/b')?.avatarTone).toBe(natural);
		expect(tones.get('__unattributed__')?.fill).toBe(AVATAR_NEUTRAL.chart);
		// Deterministic.
		expect(assignChartTones('apis', rows)).toEqual(tones);
	});

	it('mirrors a collision fallback in the breakdown row tile and bar', () => {
		const rows = [row('initech/a', 'initech-api', 10), row('umbrella/b', 'umbrella-api', 5)];
		const { container } = renderWithProviders(
			<UsageBreakdown lens="apis" onLensChange={() => {}} rows={rows} />,
		);
		const fallback = (avatarToneIndex('umbrella') + 1) % 8;
		const wrappers = [...container.querySelectorAll('[data-chart-tone]')];
		expect(wrappers.map((w) => w.getAttribute('data-chart-tone'))).toEqual([
			String(avatarToneIndex('initech')),
			String(fallback),
		]);
		const tile = wrappers[1].querySelector('[data-testid="vendor-mark"]')!;
		expect(getComputedStyle(tile).backgroundColor).toBe(
			computed(`hsl(var(--avatar-${fallback}-bg))`),
		);
		const untouched = wrappers[0].querySelector('[data-testid="vendor-mark"]')!;
		expect(getComputedStyle(untouched).backgroundColor).toBe(
			computed(avatarToneColors(avatarToneIndex('initech')).bg),
		);
	});
});
