/**
 * What the agent holds, as one small pill centred under its name: "no keys"
 * (success, a lock) in the WITH lane, "N live keys" (muted danger, a key, the
 * story APIs' marks in a tidy overlapping stack) in the WITHOUT lane. Same
 * shape in both, so the two read as a pair. The WITHOUT pill's keys are listed
 * in a tooltip, on a focusable trigger laid over it.
 */
import { KeyRound, Lock } from 'lucide-react';
import { Tooltip, VendorMark } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import { ENV_SECRETS } from '@/modules/agents/components/landing/data/demoFixtures';
import { SvgMark } from '@/modules/agents/components/landing/act1/svgParts';

const PILL_H = 18;
const PILL_MARK = 11;
const PILL_STEP = 7;
/** The pill's label is 9px mono: about 5.45px a character. */
const PILL_CHAR_W = 5.45;

export const LIVE_KEYS = `${ENV_SECRETS.length} live keys`;

function pillLabel(exposed: boolean): string {
	return exposed ? LIVE_KEYS : 'no keys';
}

/** The pill's width: icon, label, and (WITHOUT only) the mark stack. */
function pillWidth(exposed: boolean): number {
	const text = pillLabel(exposed).length * PILL_CHAR_W;
	const stack = exposed ? PILL_MARK + (ENV_SECRETS.length - 1) * PILL_STEP + 6 : 0;
	return 8 + 10 + 5 + text + stack + 9;
}

export function KeyPill({ cx, y, tone }: { cx: number; y: number; tone: 'safe' | 'exposed' }) {
	const exposed = tone === 'exposed';
	const w = pillWidth(exposed);
	const x = cx - w / 2;
	const IconCmp = exposed ? KeyRound : Lock;
	const stackX = x + w - 9 - (PILL_MARK + (ENV_SECRETS.length - 1) * PILL_STEP);
	return (
		<g data-testid={exposed ? 'key-pill' : 'no-keys-pill'} data-tone={tone}>
			<rect
				x={x}
				y={y}
				width={w}
				height={PILL_H}
				rx={PILL_H / 2}
				strokeWidth={1}
				className={
					exposed
						? 'fill-danger/10 stroke-danger/35'
						: 'fill-success/10 stroke-success/35'
				}
			/>
			<IconCmp
				x={x + 8}
				y={y + 4}
				width={10}
				height={10}
				className={exposed ? 'text-danger/90' : 'text-success'}
				aria-hidden="true"
			/>
			<text
				x={x + 23}
				y={y + 12.5}
				fontSize={9}
				className={cn('font-mono', exposed ? 'fill-danger/90' : 'fill-success')}
			>
				{pillLabel(exposed)}
			</text>
			{exposed &&
				ENV_SECRETS.map(({ api }, k) => {
					const mx = stackX + k * PILL_STEP;
					const my = y + (PILL_H - PILL_MARK) / 2;
					return (
						<g key={api} data-key-api={api}>
							{/* A 1px ring in the lane's colour separates the overlapping marks. */}
							<rect
								x={mx - 1}
								y={my - 1}
								width={PILL_MARK + 2}
								height={PILL_MARK + 2}
								rx={3.5}
								className="fill-background"
							/>
							<SvgMark slug={api} x={mx} y={my} size={PILL_MARK} />
						</g>
					);
				})}
		</g>
	);
}

/** Masked keys, for the pill's tooltip. */
function KeyList() {
	return (
		<span className="block space-y-0.5">
			<span className="text-muted-foreground block text-[11px]">
				In the agent’s .env, readable by any script it runs:
			</span>
			{ENV_SECRETS.map(({ api, line }) => (
				<span key={api} className="flex items-center gap-1.5 font-mono text-[11px]">
					<VendorMark slug={api} size="xs" />
					{line}
				</span>
			))}
		</span>
	);
}

/**
 * The WITHOUT pill's tooltip trigger, laid over the pill. It sits in the
 * lane's SVG wrapper (which has the SVG's own aspect ratio), placed in
 * percentages of the viewBox (`w` × `h`), so it follows the SVG as it scales.
 */
export function KeyPillTooltip({
	cx,
	y,
	w: viewW,
	h: viewH,
}: {
	cx: number;
	y: number;
	w: number;
	h: number;
}) {
	const w = pillWidth(true);
	const pct = (v: number, of: number) => `${(v / of) * 100}%`;
	return (
		<span
			className="absolute"
			style={{
				left: pct(cx - w / 2, viewW),
				top: pct(y, viewH),
				width: pct(w, viewW),
				height: pct(PILL_H, viewH),
			}}
		>
			<Tooltip content={<KeyList />} className="block h-full w-full">
				<span
					role="img"
					className="block h-full w-full cursor-help rounded-full"
					aria-label={`The agent holds ${LIVE_KEYS}`}
					data-testid="key-pill-trigger"
				/>
			</Tooltip>
		</span>
	);
}
