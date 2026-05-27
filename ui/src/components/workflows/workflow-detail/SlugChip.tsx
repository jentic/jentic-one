import { Hash } from 'lucide-react';
import { CopyButton } from '@/components/ui/CopyButton';

interface SlugChipProps {
	slug: string;
}

/**
 * Compact, copy-on-click chip showing a workflow's slug. Used in the
 * detail header so authors can grab the canonical id without diving
 * into the URL bar. Truncates inside its container — title attribute
 * keeps the full slug discoverable.
 */
export function SlugChip({ slug }: SlugChipProps) {
	return (
		<span
			className="border-border/50 bg-muted/40 hover:border-border hover:bg-muted/60 inline-flex max-w-full min-w-0 items-center gap-1.5 rounded-md border py-0.5 pr-0.5 pl-2 font-mono text-[11px] transition-colors"
			data-testid="workflow-slug"
			title={slug}
		>
			<Hash size={11} aria-hidden="true" className="text-muted-foreground/70 shrink-0" />
			<span className="text-foreground/90 min-w-0 truncate">{slug}</span>
			<CopyButton
				value={slug}
				size="icon"
				variant="ghost"
				toastMessage="Slug copied"
				ariaLabel="Copy slug"
				className="text-muted-foreground/70 hover:text-foreground h-6 w-6 p-0 [&_svg]:h-3 [&_svg]:w-3"
			/>
		</span>
	);
}
