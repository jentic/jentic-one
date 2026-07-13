/**
 * RailFooter — toast scope selector + audio-on-critical toggle.
 *
 * Both controls live here because they're persistent preferences. The toast
 * scope is read by ToastHost; the audio toggle is read by AgentRail itself
 * when a new critical event arrives.
 */
import { Volume2, VolumeX } from 'lucide-react';
import type { ToastScope } from '@/shared/lib/agentStream';
import { cn } from '@/shared/lib/utils';

export type RailFooterProps = {
	scope: ToastScope;
	onScopeChange: (s: ToastScope) => void;
	audioOnCritical: boolean;
	onAudioToggle: () => void;
};

export function RailFooter({
	scope,
	onScopeChange,
	audioOnCritical,
	onAudioToggle,
}: RailFooterProps) {
	return (
		<div className="border-border bg-background/60 space-y-2 border-t px-3 py-2">
			<div>
				<label
					htmlFor="agent-rail-toast-scope"
					className="text-muted-foreground mb-1 block font-mono text-[10px] tracking-widest uppercase"
				>
					Toasts
				</label>
				<select
					id="agent-rail-toast-scope"
					value={scope}
					onChange={(e) => onScopeChange(e.target.value as ToastScope)}
					className="bg-muted border-border text-foreground w-full rounded border px-2 py-1 text-xs"
				>
					<option value="all">All events</option>
					<option value="warning">Warning &amp; up</option>
					<option value="critical">Critical only</option>
					<option value="off">Off</option>
				</select>
			</div>
			<button
				type="button"
				onClick={onAudioToggle}
				aria-pressed={audioOnCritical}
				className={cn(
					'flex w-full items-center gap-2 rounded border px-2 py-1 font-mono text-[11px] tracking-wider transition-colors',
					audioOnCritical
						? 'border-danger/40 bg-danger/10 text-danger'
						: 'border-border text-muted-foreground hover:text-foreground',
				)}
				title={
					audioOnCritical
						? 'Audio cue on critical events: ON'
						: 'Audio cue on critical events: OFF'
				}
			>
				{audioOnCritical ? (
					<Volume2 className="h-3 w-3" />
				) : (
					<VolumeX className="h-3 w-3" />
				)}
				Audio on critical
			</button>
		</div>
	);
}
