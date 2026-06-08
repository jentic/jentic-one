import { type JSX } from 'react';
import { Check, X, Clock, Loader2, AlertCircle } from 'lucide-react';
import type { ExecutionStatus } from '@/components/monitor/types';
import { cn } from '@/lib/utils';

interface StatusPillProps {
	status: ExecutionStatus;
	className?: string;
}

interface PillConfig {
	icon: JSX.Element;
	label: string;
	cls: string;
}

const STATUS_CONFIG: Record<ExecutionStatus, PillConfig> = {
	COMPLETED: {
		icon: <Check className="h-3 w-3" />,
		label: 'Done',
		cls: 'bg-accent-green/10 text-accent-green',
	},
	FAILED: {
		icon: <X className="h-3 w-3" />,
		label: 'Failed',
		cls: 'bg-accent-red/10 text-accent-red',
	},
	RUNNING: {
		icon: <Loader2 className="h-3 w-3 animate-spin" />,
		label: 'Running',
		cls: 'bg-accent-blue/10 text-accent-blue',
	},
	QUEUED: {
		icon: <Clock className="h-3 w-3" />,
		label: 'Queued',
		cls: 'bg-muted text-muted-foreground',
	},
	PRE_CHECK: {
		icon: <AlertCircle className="h-3 w-3" />,
		label: 'Pre-check',
		cls: 'bg-accent-amber/10 text-accent-amber',
	},
};

/**
 * Compact status pill — icon + label in a coloured rounded chip.
 *
 * Centralised so the sidebar, table, and detail sheet stop diverging:
 * the webapp had three near-identical inline copies of this component.
 */
export function StatusPill({ status, className }: StatusPillProps): JSX.Element {
	const cfg = STATUS_CONFIG[status] ?? STATUS_CONFIG.QUEUED;
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
