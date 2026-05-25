import { useRef, useState, useEffect, useCallback, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

interface TruncateWithTooltipProps {
	children: ReactNode;
	className?: string;
}

/**
 * Renders children in a single truncated line. When the content overflows,
 * hovering shows a fixed-position tooltip with the full text that escapes
 * any overflow-hidden ancestors.
 */
export function TruncateWithTooltip({ children, className = '' }: TruncateWithTooltipProps) {
	const ref = useRef<HTMLSpanElement>(null);
	const [overflows, setOverflows] = useState(false);
	const [show, setShow] = useState(false);
	const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

	useEffect(() => {
		const el = ref.current;
		if (!el) return;
		const check = () => setOverflows(el.scrollWidth > el.clientWidth);
		check();
		const ro = new ResizeObserver(check);
		ro.observe(el);
		return () => ro.disconnect();
	}, [children]);

	const handleEnter = useCallback(() => {
		if (!overflows || !ref.current) return;
		const rect = ref.current.getBoundingClientRect();
		setPos({ top: rect.bottom + 4, left: rect.left });
		setShow(true);
	}, [overflows]);

	const handleLeave = useCallback(() => {
		setShow(false);
	}, []);

	return (
		<span
			ref={ref}
			className={`block truncate ${className}`}
			onMouseEnter={handleEnter}
			onMouseLeave={handleLeave}
		>
			{children}
			{show &&
				pos &&
				createPortal(
					<span
						role="tooltip"
						className="bg-popover/95 text-popover-foreground border-border pointer-events-none fixed z-[9999] max-w-[320px] rounded-md border px-2.5 py-1.5 text-xs whitespace-normal shadow-lg backdrop-blur-sm"
						style={{ top: pos.top, left: pos.left }}
					>
						{children}
					</span>,
					document.body,
				)}
		</span>
	);
}
