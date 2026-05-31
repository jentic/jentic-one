import { CheckCircle2, Globe } from 'lucide-react';
import type { DiscoveryEntity } from './DiscoveryCard';

export type CardStatus = 'imported' | 'available';

interface CardStatusPillProps {
	source: DiscoveryEntity['source'];
	size?: 'sm' | 'md';
	className?: string;
}

export function deriveCardStatus({ source }: Pick<CardStatusPillProps, 'source'>): CardStatus {
	return source === 'workspace' ? 'imported' : 'available';
}

interface PillSpec {
	label: string;
	icon: typeof CheckCircle2;
	cls: string;
	testId: string;
}

const SPEC: Record<CardStatus, PillSpec> = {
	imported: {
		label: 'Imported',
		icon: CheckCircle2,
		cls: 'bg-emerald-500/15 text-emerald-300 ring-emerald-500/30',
		testId: 'card-status-imported',
	},
	available: {
		label: 'Available',
		icon: Globe,
		cls: 'border-border/70 bg-transparent text-muted-foreground ring-border/60',
		testId: 'card-status-available',
	},
};

export function CardStatusPill({ source, size = 'md', className }: CardStatusPillProps) {
	const status = deriveCardStatus({ source });
	const spec = SPEC[status];
	const Icon = spec.icon;
	const sizeCls = size === 'sm' ? 'px-2 py-0 text-[11px]' : 'px-2.5 py-0.5 text-xs';
	return (
		<span
			data-testid={spec.testId}
			className={`inline-flex shrink-0 items-center gap-1 rounded-full font-medium whitespace-nowrap ring-1 ${sizeCls} ${spec.cls} ${className ?? ''}`}
		>
			<Icon size={11} aria-hidden="true" />
			{spec.label}
		</span>
	);
}
