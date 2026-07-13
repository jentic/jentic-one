/**
 * Shared section primitives for the docs narrative pages.
 *
 * `DocsSection` wraps each top-level section with a consistent anchored heading
 * and spacing, so the scroll-spy and sidebar line up with what's on screen.
 * `Lead` and `Prose` are light typographic helpers for the narrative copy.
 */
import type { ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import { cn } from '@/shared/lib/utils';

export function DocsSectionBlock({
	id,
	title,
	icon: Icon,
	intro,
	children,
}: {
	id: string;
	title: string;
	icon?: LucideIcon;
	intro?: ReactNode;
	children: ReactNode;
}) {
	return (
		<section id={id} aria-labelledby={`${id}-heading`} className="scroll-mt-20">
			<div className="mb-4">
				<h2
					id={`${id}-heading`}
					className="font-heading text-foreground flex items-center gap-2.5 text-2xl font-bold"
				>
					{Icon && <Icon className="text-primary h-6 w-6" aria-hidden="true" />}
					{title}
				</h2>
				{intro && <p className="text-foreground/65 mt-2 max-w-2xl text-[15px]">{intro}</p>}
			</div>
			<div className="space-y-5">{children}</div>
		</section>
	);
}

export function Prose({ children, className }: { children: ReactNode; className?: string }) {
	return (
		<div
			className={cn(
				'text-foreground/75 max-w-2xl space-y-3 text-[15px] leading-relaxed',
				className,
			)}
		>
			{children}
		</div>
	);
}
