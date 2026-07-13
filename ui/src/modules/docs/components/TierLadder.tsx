/**
 * TierLadder — the cascade graphic for scope tiers.
 *
 * A left-to-right flow (Admin → Write → Execute → Read) that makes the core
 * mental model obvious at a glance: holding a broader tier *grants* every
 * narrower one, so you assign the highest level a caller needs and nothing
 * more. Purely presentational — no data dependency.
 */
import { Crown, Pencil, Eye, Zap, ArrowRight } from 'lucide-react';
import { cn } from '@/shared/lib/utils';

const TIERS = [
	{
		key: 'admin',
		label: 'Admin',
		meaning: 'Manage everything',
		text: 'text-danger',
		bg: 'bg-danger/10',
		border: 'border-danger/40',
		Icon: Crown,
	},
	{
		key: 'write',
		label: 'Write',
		meaning: 'Create & modify',
		text: 'text-accent-orange',
		bg: 'bg-accent-orange/10',
		border: 'border-accent-orange/40',
		Icon: Pencil,
	},
	{
		key: 'execute',
		label: 'Execute',
		meaning: 'Run via broker',
		text: 'text-accent-blue',
		bg: 'bg-accent-blue/10',
		border: 'border-accent-blue/40',
		Icon: Zap,
	},
	{
		key: 'read',
		label: 'Read',
		meaning: 'View only',
		text: 'text-accent-teal',
		bg: 'bg-accent-teal/10',
		border: 'border-accent-teal/40',
		Icon: Eye,
	},
] as const;

export function TierLadder() {
	return (
		<div className="border-border bg-card/40 rounded-xl border p-4">
			<p className="text-foreground/55 mb-3 text-xs">
				Scopes are tiered. A broader tier{' '}
				<strong className="text-foreground/80">grants</strong> every narrower one, so you
				only assign the highest level a caller needs.
			</p>
			<div className="flex flex-wrap items-stretch gap-2">
				{TIERS.map((t, i) => (
					<div key={t.key} className="flex items-center gap-2">
						<div
							className={cn(
								'flex min-w-[7rem] flex-col gap-1 rounded-lg border px-3 py-2',
								t.border,
								t.bg,
							)}
						>
							<div
								className={cn(
									'flex items-center gap-1.5 text-sm font-semibold',
									t.text,
								)}
							>
								<t.Icon className="h-4 w-4" aria-hidden="true" />
								{t.label}
							</div>
							<span className="text-foreground/65 text-[11px]">{t.meaning}</span>
						</div>
						{i < TIERS.length - 1 && (
							<ArrowRight
								className="text-foreground/30 h-4 w-4 shrink-0"
								aria-hidden="true"
							/>
						)}
					</div>
				))}
			</div>
		</div>
	);
}
