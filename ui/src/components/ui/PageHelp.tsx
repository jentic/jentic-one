import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { HelpCircle } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Dialog } from '@/components/ui/Dialog';
import { Kbd } from '@/components/ui/Kbd';
import type { KeyboardShortcut } from '@/components/ui/KeyboardShortcutsBar';
import { cn } from '@/lib/utils';

export interface PageHelpSection {
	/** Optional eyebrow / heading shown above the body. */
	heading?: string;
	/**
	 * Body content. Free-form ReactNode so callers can pass `<p>`,
	 * lists, code samples, links, etc. Kept as ReactNode rather than a
	 * markdown string so contributors don't have to think about
	 * sanitisation for help blurbs they wrote inline.
	 */
	body: ReactNode;
}

export interface PageHelpLink {
	href: string;
	label: string;
	/**
	 * When true, force a new tab + `noreferrer noopener`. Defaults to
	 * auto-detect based on the URL (absolute http(s) → external).
	 */
	external?: boolean;
}

export interface PageHelpProps {
	/** Dialog title, e.g. "About Discover". */
	title: string;
	/**
	 * Top-level prose blurb shown above sections — the elevator pitch
	 * for "what is this page for?". Optional; some pages can skip it
	 * and lead with sections.
	 */
	intro?: ReactNode;
	/** Long-form content blocks rendered in order under the intro. */
	sections?: PageHelpSection[];
	/**
	 * Keyboard shortcut map. When provided we render a "Keyboard
	 * shortcuts" sub-section automatically using the same `Kbd`
	 * primitive as the bottom hints bar — no need for callers to
	 * compose two help surfaces with the same data.
	 */
	shortcuts?: KeyboardShortcut[];
	/** Optional follow-up link list (docs, tutorials, etc.). */
	links?: PageHelpLink[];
	/**
	 * Accessible label for the trigger button. Defaults to
	 * `"Help for ${title}"` so screen readers don't read out a bare
	 * "Help" when several helps live on the same surface.
	 */
	triggerAriaLabel?: string;
	/**
	 * Tailwind classes appended to the trigger button. Useful when the
	 * default ghost style needs to invert against a brighter
	 * background.
	 */
	triggerClassName?: string;
	/**
	 * When true (default) bind a global `⌘ /` (Ctrl+/ on non-Mac)
	 * keypress to open this help surface. Set to `false` if a page
	 * already binds that combo to something else.
	 */
	bindShortcut?: boolean;
}

/**
 * Per-page contextual help: a small `?` button (drop into
 * `PageHeader.actions`) that opens a dialog with the page's
 * elevator pitch, free-form sections, keyboard shortcuts, and
 * follow-up links.
 *
 * Shape rationale: every dashboard surface in jentic-mini ends up
 * needing roughly the same affordances —
 *
 *   1. "what does this page do" prose,
 *   2. a few task-oriented blurbs ("how do I X"),
 *   3. its keyboard shortcuts,
 *   4. links to deeper docs.
 *
 * Encoding all four as typed slots makes call-sites cheap and means
 * future-us can change the visual design (e.g. switch to a popover,
 * add tabs, render a TOC) in one place without rewriting every
 * caller. ReactNode bodies sidestep the markdown-vs-plaintext
 * tradeoff for short blurbs authored inline by engineers.
 *
 * The component owns its own open state and (optionally) listens to
 * a global `?` key — so the trigger button and the keyboard
 * shortcut converge on the same dialog. Callers don't need to wire
 * `useState` themselves.
 */
export function PageHelp({
	title,
	intro,
	sections = [],
	shortcuts = [],
	links = [],
	triggerAriaLabel,
	triggerClassName,
	bindShortcut = true,
}: PageHelpProps) {
	const [open, setOpen] = useState(false);

	const onClose = useCallback(() => setOpen(false), []);

	useEffect(() => {
		if (!bindShortcut) return;
		const handler = (e: KeyboardEvent) => {
			if (e.key === '/' && (e.metaKey || e.ctrlKey)) {
				const el = e.target;
				if (
					el instanceof HTMLElement &&
					(el.tagName === 'INPUT' ||
						el.tagName === 'TEXTAREA' ||
						el.tagName === 'SELECT' ||
						el.isContentEditable)
				)
					return;
				e.preventDefault();
				setOpen(true);
			}
		};
		window.addEventListener('keydown', handler);
		return () => window.removeEventListener('keydown', handler);
	}, [bindShortcut]);

	const hasShortcuts = shortcuts.length > 0;
	const hasLinks = links.length > 0;
	const hasSections = sections.length > 0;
	const hasIntro = intro != null;

	return (
		<>
			<Button
				variant="ghost"
				size="icon"
				onClick={() => setOpen(true)}
				aria-label={triggerAriaLabel ?? `Help for ${title}`}
				className={cn('text-muted-foreground hover:text-foreground', triggerClassName)}
				data-testid="page-help-trigger"
			>
				<HelpCircle className="h-4 w-4" />
			</Button>

			<Dialog open={open} onClose={onClose} title={title} size="lg">
				<div className="space-y-5 text-sm" data-testid="page-help-content">
					{hasIntro && (
						<div className="text-muted-foreground leading-relaxed">{intro}</div>
					)}

					{hasSections && (
						<div className="space-y-4">
							{sections.map((section, idx) => (
								<section key={section.heading ?? idx}>
									{section.heading && (
										<h3 className="text-foreground mb-1 text-sm font-semibold">
											{section.heading}
										</h3>
									)}
									<div className="text-muted-foreground leading-relaxed">
										{section.body}
									</div>
								</section>
							))}
						</div>
					)}

					{hasShortcuts && (
						<section>
							<h3 className="text-foreground mb-2 text-sm font-semibold">
								Keyboard shortcuts
							</h3>
							<ul
								className="border-border/40 divide-border/40 divide-y rounded-md border"
								data-testid="page-help-shortcuts"
							>
								{shortcuts.map((s) => (
									<li
										key={s.label}
										className="flex items-center justify-between gap-3 px-3 py-2"
									>
										<span className="text-muted-foreground">{s.label}</span>
										<span className="flex shrink-0 items-center gap-1">
											{s.keys.map((k, idx) => (
												<span
													key={`${s.label}-${idx}-${k}`}
													className="flex items-center gap-1"
												>
													{s.chord && idx > 0 && (
														<span className="text-muted-foreground/50 text-xs">
															+
														</span>
													)}
													<Kbd size="md" variant="solid">
														{k}
													</Kbd>
												</span>
											))}
											{s.altKeys && (
												<>
													<span className="text-muted-foreground/60 mx-1 text-[10px] uppercase">
														or
													</span>
													{s.altKeys.map((k, idx) => (
														<Kbd
															key={`${s.label}-alt-${idx}-${k}`}
															size="md"
															variant="solid"
														>
															{k}
														</Kbd>
													))}
												</>
											)}
										</span>
									</li>
								))}
							</ul>
						</section>
					)}

					{hasLinks && (
						<section>
							<h3 className="text-foreground mb-2 text-sm font-semibold">
								Learn more
							</h3>
							<ul className="space-y-1.5">
								{links.map((link) => {
									// Auto-detect external unless explicitly forced. We
									// don't lean on `AppLink` here so this component
									// can live in `components/ui` without dragging in
									// router primitives — it's an island, on purpose.
									const isExternal = link.external ?? /^https?:/i.test(link.href);
									return (
										<li key={link.href}>
											<a
												href={link.href}
												target={isExternal ? '_blank' : undefined}
												rel={isExternal ? 'noreferrer noopener' : undefined}
												className="text-accent-teal hover:underline"
											>
												{link.label}
											</a>
										</li>
									);
								})}
							</ul>
						</section>
					)}

					{!hasIntro && !hasSections && !hasShortcuts && !hasLinks && (
						<p className="text-muted-foreground italic">
							No help content has been provided for this page yet.
						</p>
					)}
				</div>
			</Dialog>
		</>
	);
}
