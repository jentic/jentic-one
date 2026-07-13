/**
 * DocsMobileNav — the section navigator for narrow screens.
 *
 * The desktop sidebar (`DocsSidebar`) is hidden below `lg`; this replaces it
 * with a sticky top bar carrying the current-section label and a dropdown of
 * every section so the reader can jump around without an offscreen rail.
 */
import { useState } from 'react';
import { ChevronDown, Menu } from 'lucide-react';
import { DOCS_NAV, DOCS_SECTIONS } from '@/modules/docs/lib/nav';
import { useDismissable } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';

export interface DocsMobileNavProps {
	activeId: string | null;
	onNavigate: (id: string) => void;
}

export function DocsMobileNav({ activeId, onNavigate }: DocsMobileNavProps) {
	const [open, setOpen] = useState(false);
	const ref = useDismissable<HTMLDivElement>(open, () => setOpen(false));
	const current = DOCS_SECTIONS.find((s) => s.id === activeId) ?? DOCS_SECTIONS[0];

	return (
		<div
			ref={ref}
			className="bg-background/95 border-border -mx-page-gutter px-page-gutter sticky top-14 z-30 mb-4 border-b py-2 backdrop-blur lg:hidden"
		>
			<button
				type="button"
				onClick={() => setOpen((v) => !v)}
				aria-expanded={open}
				aria-haspopup="menu"
				className="border-border bg-card/60 text-foreground flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium"
			>
				<Menu className="text-primary h-4 w-4 shrink-0" aria-hidden="true" />
				<span className="text-foreground/50 text-xs">Docs</span>
				<span className="text-foreground/30">/</span>
				<span className="truncate">{current?.label}</span>
				<ChevronDown
					className={cn(
						'text-foreground/40 ml-auto h-4 w-4 shrink-0 transition-transform',
						open && 'rotate-180',
					)}
					aria-hidden="true"
				/>
			</button>

			{open && (
				<div
					role="menu"
					className="border-border bg-card mt-2 max-h-[60vh] space-y-3 overflow-y-auto rounded-lg border p-2 shadow-lg"
				>
					{DOCS_NAV.map((group) => (
						<div key={group.title}>
							<p className="text-foreground/40 px-2 py-1 text-[11px] font-semibold tracking-wider uppercase">
								{group.title}
							</p>
							{group.sections.map((section) => {
								const active = section.id === activeId;
								return (
									<button
										key={section.id}
										type="button"
										role="menuitem"
										aria-current={active ? 'location' : undefined}
										onClick={() => {
											onNavigate(section.id);
											setOpen(false);
										}}
										className={cn(
											'flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm transition-colors',
											active
												? 'bg-primary/10 text-primary font-medium'
												: 'text-foreground/70 hover:bg-muted hover:text-foreground',
										)}
									>
										<section.icon
											className="h-4 w-4 shrink-0"
											aria-hidden="true"
										/>
										{section.label}
									</button>
								);
							})}
						</div>
					))}
				</div>
			)}
		</div>
	);
}
