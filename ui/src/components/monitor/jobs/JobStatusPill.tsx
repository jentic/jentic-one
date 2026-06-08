import { type JSX } from 'react';
import { Check, X, Clock, Loader2, CloudUpload, Ban } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { JobLogEntry } from '@/components/monitor/types';

/**
 * Status pill for the Jobs tab.
 *
 * Mirrors `StatusPill` from `monitor/shared/` but speaks the job vocabulary —
 * notably "Cancelled" (synthesised by `jobToJobLogEntry`) and "Upstream" (the
 * RFC-7240 case where the upstream API itself returned 202). We don't reuse
 * `StatusPill` because its label set is locked to the executions enum.
 */
interface JobStatusPillProps {
	status: JobLogEntry['status'];
	className?: string;
}

interface PillConfig {
	icon: JSX.Element;
	label: string;
	cls: string;
}

const STATUS_CONFIG: Record<JobLogEntry['status'], PillConfig> = {
	pending: {
		icon: <Clock className="h-3 w-3" />,
		label: 'Pending',
		cls: 'bg-muted text-muted-foreground',
	},
	running: {
		icon: <Loader2 className="h-3 w-3 animate-spin" />,
		label: 'Running',
		cls: 'bg-accent-blue/10 text-accent-blue',
	},
	complete: {
		icon: <Check className="h-3 w-3" />,
		label: 'Done',
		cls: 'bg-accent-green/10 text-accent-green',
	},
	failed: {
		icon: <X className="h-3 w-3" />,
		label: 'Failed',
		cls: 'bg-accent-red/10 text-accent-red',
	},
	upstream_async: {
		icon: <CloudUpload className="h-3 w-3" />,
		label: 'Upstream',
		cls: 'bg-accent-amber/10 text-accent-amber',
	},
	cancelled: {
		icon: <Ban className="h-3 w-3" />,
		label: 'Cancelled',
		cls: 'bg-muted text-muted-foreground',
	},
};

export function JobStatusPill({ status, className }: JobStatusPillProps): JSX.Element {
	const cfg = STATUS_CONFIG[status] ?? STATUS_CONFIG.pending;
	return (
		<span
			className={cn(
				'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium',
				cfg.cls,
				className,
			)}
		>
			{cfg.icon}
			{cfg.label}
		</span>
	);
}
