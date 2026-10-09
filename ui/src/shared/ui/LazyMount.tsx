/**
 * LazyMount — render heavy children only when they're near the viewport.
 *
 * The API reference mounts ~100 operations and ~160 model schemas; eagerly
 * rendering every parameter table, schema tree, and permission panel is tens of
 * thousands of DOM nodes and React work up front, which is what makes the page
 * janky. This wrapper keeps the *anchor element* (with its `id`) always in the
 * DOM — so scroll-spy and hash navigation keep working — but swaps the expensive
 * subtree for a cheap fixed-height placeholder until an IntersectionObserver
 * says it's within `rootMargin` of the viewport.
 *
 * Once shown it stays mounted (`once`, the default) so scrolling back up never
 * re-pays the cost or loses element state. `minHeight` keeps the scrollbar and
 * anchor offsets stable before real content measures in.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react';

export interface LazyMountProps {
	id?: string;
	className?: string;
	/** Placeholder height before the real content mounts (CSS length). */
	minHeight?: number | string;
	/** How far outside the viewport to start mounting. Default 800px band. */
	rootMargin?: string;
	/** Keep children mounted once shown (default true). */
	once?: boolean;
	children: ReactNode;
}

export function LazyMount({
	id,
	className,
	minHeight = 240,
	rootMargin = '800px 0px 800px 0px',
	once = true,
	children,
}: LazyMountProps) {
	const ref = useRef<HTMLDivElement>(null);
	const [visible, setVisible] = useState(false);

	useEffect(() => {
		const el = ref.current;
		if (!el) return;
		// No IO (old browsers / tests): render eagerly.
		if (typeof IntersectionObserver === 'undefined') {
			setVisible(true);
			return;
		}
		const io = new IntersectionObserver(
			(entries) => {
				for (const entry of entries) {
					if (entry.isIntersecting) {
						setVisible(true);
						if (once) io.disconnect();
					} else if (!once) {
						setVisible(false);
					}
				}
			},
			{ rootMargin },
		);
		io.observe(el);
		return () => io.disconnect();
	}, [rootMargin, once]);

	return (
		<div
			ref={ref}
			id={id}
			className={className}
			style={
				visible
					? undefined
					: {
							minHeight: typeof minHeight === 'number' ? `${minHeight}px` : minHeight,
						}
			}
		>
			{visible ? children : null}
		</div>
	);
}
