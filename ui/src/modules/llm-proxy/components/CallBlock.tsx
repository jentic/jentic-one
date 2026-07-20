/**
 * Tool-call presentation for the trace-flow.
 *
 * `CallBlock` — one tool call rendered as a compact CHIP in an agent's left→right
 * flow. Colour-coded by governance verdict + outcome:
 *   • allow + completed          → green
 *   • deny                       → red
 *   • allow + failed/!completed  → amber (error)
 * Destructive calls carry a small warning glyph. Clicking opens the deep-dive.
 *
 * `CallChain` — the horizontal left→right sequence of `CallBlock` chips, joined
 * by small arrow icons and merging into a small "output" terminal pill. This is
 * the primary per-agent call presentation. Falls back to an "orchestration only"
 * placeholder when the agent made no tool calls.
 *
 * `callTone` remains the single source of truth for how a call maps to a visual
 * tone and is reused by the compact per-agent summary on the node card.
 */
import { motion } from 'framer-motion';
import { ArrowRight, CircleDot, TriangleAlert } from 'lucide-react';
import { MethodBadge } from '@/shared/ui';
import type { ProxyCall } from '@/modules/llm-proxy/api';
import { formatDuration } from '@/modules/llm-proxy/lib/format';
import { cn } from '@/shared/lib/utils';

export type CallTone = 'allow' | 'deny' | 'error';

/** The single source of truth for how a call maps to a visual tone. */
export function callTone(call: ProxyCall): CallTone {
	if (call.verdict === 'deny' || call.status === 'denied') return 'deny';
	if (call.status !== 'completed') return 'error';
	return 'allow';
}

const TONE_CLASS: Record<CallTone, string> = {
	allow: 'border-success/40 bg-success/10 hover:bg-success/20 hover:border-success/60',
	deny: 'border-danger/40 bg-danger/10 hover:bg-danger/20 hover:border-danger/60',
	error: 'border-warning/40 bg-warning/10 hover:bg-warning/20 hover:border-warning/60',
};

const TONE_DOT: Record<CallTone, string> = {
	allow: 'bg-success',
	deny: 'bg-danger',
	error: 'bg-warning',
};

interface CallBlockProps {
	call: ProxyCall;
	onOpen: (call: ProxyCall) => void;
	/** Stagger index for the enter animation. */
	index?: number;
}

/** Trim a path to keep the block compact but still recognisable. */
function shortPath(path: string): string {
	if (path.length <= 26) return path;
	return `…${path.slice(-25)}`;
}

export function CallBlock({ call, onOpen, index = 0 }: CallBlockProps) {
	const tone = callTone(call);

	return (
		<motion.button
			type="button"
			initial={{ opacity: 0, scale: 0.9 }}
			animate={{ opacity: 1, scale: 1 }}
			transition={{ duration: 0.2, delay: Math.min(index * 0.02, 0.3), ease: 'easeOut' }}
			whileHover={{ y: -2 }}
			onClick={() => onOpen(call)}
			title={`${call.method} ${call.path} — ${call.summary}`}
			className={cn(
				'group focus-visible:ring-ring relative flex w-[168px] shrink-0 flex-col gap-1.5 rounded-lg border px-2.5 py-2 text-left transition-colors focus-visible:ring-2 focus-visible:outline-none',
				TONE_CLASS[tone],
			)}
		>
			<span
				className={cn(
					'absolute -top-1 -right-1 h-2.5 w-2.5 rounded-full ring-2 ring-white/10',
					TONE_DOT[tone],
				)}
				aria-hidden="true"
			/>
			<div className="flex items-center gap-1.5">
				<MethodBadge method={call.method} />
				{call.destructive && (
					<TriangleAlert
						className="text-warning h-3.5 w-3.5 shrink-0"
						aria-label="Destructive call"
					/>
				)}
			</div>
			<div className="text-foreground truncate font-mono text-[11px] leading-tight">
				{shortPath(call.path)}
			</div>
			<div className="text-muted-foreground/70 flex items-center justify-between text-[10px]">
				<span className="truncate">{call.api_name}</span>
				<span className="shrink-0 tabular-nums">{formatDuration(call.duration_ms)}</span>
			</div>
		</motion.button>
	);
}

interface CallChainProps {
	calls: ProxyCall[];
	onOpenCall: (call: ProxyCall) => void;
}

/** The left→right call chain that merges into the agent's output terminal. */
export function CallChain({ calls, onOpenCall }: CallChainProps) {
	if (calls.length === 0) {
		return (
			<div className="text-muted-foreground/60 border-border/40 rounded-lg border border-dashed px-3 py-1.5 text-[11px] italic">
				No tool calls — orchestration only
			</div>
		);
	}
	return (
		<div className="flex items-center gap-2">
			<div className="flex items-center gap-2 overflow-visible">
				{calls.map((call, i) => (
					<div key={call.call_id} className="flex items-center gap-2">
						{i > 0 && <ArrowRight className="text-border h-3.5 w-3.5 shrink-0" />}
						<CallBlock call={call} onOpen={onOpenCall} index={i} />
					</div>
				))}
			</div>
			{/* merge → output terminal */}
			<ArrowRight className="text-border h-4 w-4 shrink-0" />
			<motion.div
				initial={{ opacity: 0, scale: 0.8 }}
				animate={{ opacity: 1, scale: 1 }}
				transition={{ duration: 0.2, delay: Math.min(calls.length * 0.02, 0.3) }}
				className="border-primary/40 bg-primary/10 text-primary flex h-9 shrink-0 items-center gap-1.5 rounded-full border px-3"
				title="Agent output"
			>
				<CircleDot className="h-3.5 w-3.5" />
				<span className="text-[11px] font-semibold">output</span>
			</motion.div>
		</div>
	);
}
