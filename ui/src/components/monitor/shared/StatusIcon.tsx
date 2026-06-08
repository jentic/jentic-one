import { type JSX } from 'react';
import { Check, X, Clock, Loader2, AlertCircle } from 'lucide-react';
import type { ExecutionStatus } from '@/components/monitor/types';
import { cn } from '@/lib/utils';

interface StatusIconProps {
	status: ExecutionStatus;
	size?: 'sm' | 'md' | 'lg';
	className?: string;
}

const sizeClasses = {
	sm: 'h-4 w-4',
	md: 'h-5 w-5',
	lg: 'h-6 w-6',
};

export function StatusIcon({ status, size = 'md', className }: StatusIconProps): JSX.Element {
	const iconClass = cn(sizeClasses[size], className);

	switch (status) {
		case 'COMPLETED':
			return (
				<div className="bg-accent-green/10 flex items-center justify-center rounded-full p-1">
					<Check className={cn(iconClass, 'text-accent-green')} />
				</div>
			);
		case 'FAILED':
			return (
				<div className="bg-accent-red/10 flex items-center justify-center rounded-full p-1">
					<X className={cn(iconClass, 'text-accent-red')} />
				</div>
			);
		case 'RUNNING':
			return (
				<div className="bg-accent-blue/10 flex items-center justify-center rounded-full p-1">
					<Loader2 className={cn(iconClass, 'text-accent-blue animate-spin')} />
				</div>
			);
		case 'QUEUED':
			return (
				<div className="bg-muted flex items-center justify-center rounded-full p-1">
					<Clock className={cn(iconClass, 'text-muted-foreground')} />
				</div>
			);
		case 'PRE_CHECK':
			return (
				<div className="bg-accent-amber/10 flex items-center justify-center rounded-full p-1">
					<AlertCircle className={cn(iconClass, 'text-accent-amber')} />
				</div>
			);
		default:
			return (
				<div className="bg-muted flex items-center justify-center rounded-full p-1">
					<Clock className={cn(iconClass, 'text-muted-foreground')} />
				</div>
			);
	}
}

export function getStatusLabel(status: ExecutionStatus): string {
	switch (status) {
		case 'COMPLETED':
			return 'Completed';
		case 'FAILED':
			return 'Failed';
		case 'RUNNING':
			return 'Running';
		case 'QUEUED':
			return 'Queued';
		case 'PRE_CHECK':
			return 'Pre-check';
		default:
			return status;
	}
}

export function getStatusColor(status: ExecutionStatus): string {
	switch (status) {
		case 'COMPLETED':
			return 'text-accent-green';
		case 'FAILED':
			return 'text-accent-red';
		case 'RUNNING':
			return 'text-accent-blue';
		case 'QUEUED':
			return 'text-muted-foreground';
		case 'PRE_CHECK':
			return 'text-accent-amber';
		default:
			return 'text-muted-foreground';
	}
}
