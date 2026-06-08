import { useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Ban, X } from 'lucide-react';
import { ToolkitDetailBody } from './ToolkitDetailBody';
import { api } from '@/api/client';
import { Button } from '@/components/ui/Button';
import { CopyButton } from '@/components/ui/CopyButton';
import { SheetPrimitive } from '@/components/ui/SheetPrimitive';
import { VendorIcon } from '@/components/discovery/VendorIcon';

/**
 * Right-side slide-over for a toolkit's detail view.
 *
 * Wraps the shared `ToolkitDetailBody` (the same content the `/toolkits/:id`
 * route renders) in a `SheetPrimitive`, with a pinned identity header in the
 * `ApiDetailSheetHeader` idiom — `VendorIcon` + name + copyable ID + a status
 * pill whose tokens/labels match `ToolkitCard` (SUSPENDED / simulate).
 *
 * Wider than the 480px credential sheet (640px) because toolkit detail is
 * denser: API keys, bound credentials, and per-credential permissions.
 *
 * Lifecycle mirrors `CredentialEditSheet`: the host owns the open state
 * (typically via `useToolkitDetailSheet`) and passes the sticky id so the
 * body stays mounted through the 300ms close animation.
 */
export interface ToolkitDetailSheetProps {
	/** Toolkit to render. Pass `null` for the closed state. */
	toolkitId: string | null;
	open: boolean;
	onClose: () => void;
	onAfterClose?: () => void;
}

export function ToolkitDetailSheet({
	toolkitId,
	open,
	onClose,
	onAfterClose,
}: ToolkitDetailSheetProps) {
	const headingId = 'toolkit-detail-sheet-title';
	const closeButtonRef = useRef<HTMLButtonElement | null>(null);

	const { data: toolkit } = useQuery({
		queryKey: ['toolkit', toolkitId],
		queryFn: () => api.getToolkit(toolkitId!),
		enabled: !!toolkitId,
	});

	const name = toolkit?.name ?? toolkitId ?? 'Toolkit';
	const disabled = !!toolkit?.disabled;
	const simulate = !!toolkit?.simulate;

	return (
		<SheetPrimitive
			open={open}
			onClose={onClose}
			onAfterClose={onAfterClose}
			side="right"
			ariaLabelledBy={headingId}
			initialFocus={closeButtonRef}
			className="sm:w-[640px] sm:max-w-[92vw]"
		>
			<div className="flex h-full flex-col">
				<header className="border-border/60 bg-card border-b">
					<div className="flex items-start gap-3 p-4 sm:p-5">
						<VendorIcon
							name={name}
							size="lg"
							className={disabled ? 'opacity-50 grayscale' : undefined}
						/>
						<div className="min-w-0 flex-1">
							<p className="text-primary/75 font-mono text-[10px] tracking-widest uppercase">
								Toolkit
							</p>
							<h2
								id={headingId}
								className="text-foreground mt-0.5 truncate text-base leading-tight font-semibold"
							>
								{name}
							</h2>
							{toolkit?.id && (
								<div className="mt-0.5 flex items-center gap-1.5">
									<code className="text-muted-foreground truncate font-mono text-xs">
										{toolkit.id}
									</code>
									<CopyButton value={toolkit.id} size="icon" variant="ghost" />
								</div>
							)}
							{(disabled || simulate) && (
								<div className="mt-2 flex flex-wrap items-center gap-1.5">
									{disabled && (
										<span className="bg-danger/10 text-danger border-danger/30 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px]">
											<Ban className="h-3 w-3" aria-hidden="true" />
											SUSPENDED
										</span>
									)}
									{simulate && (
										<span className="bg-primary/10 text-primary border-primary/20 inline-flex items-center rounded-full border px-2 py-0.5 font-mono text-[10px]">
											simulate
										</span>
									)}
								</div>
							)}
						</div>
						<Button
							ref={closeButtonRef}
							variant="ghost"
							size="icon"
							aria-label="Close detail panel"
							onClick={onClose}
							className="text-muted-foreground hover:text-foreground shrink-0"
						>
							<X className="h-4 w-4" />
						</Button>
					</div>
				</header>

				<div className="flex-1 overflow-y-auto px-4 py-4 sm:px-5 sm:py-5">
					{toolkitId && (
						<ToolkitDetailBody
							toolkitId={toolkitId}
							layout="sheet"
							onRequestClose={onClose}
						/>
					)}
				</div>
			</div>
		</SheetPrimitive>
	);
}
