import React, { useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { Button } from './Button';

type Variant = 'danger' | 'default';

interface ConfirmInlineProps {
	onConfirm: () => void;
	message: string;
	confirmLabel?: string;
	variant?: Variant;
	children: React.ReactElement;
}

export function ConfirmInline({
	onConfirm,
	message,
	confirmLabel = 'Confirm',
	variant = 'danger',
	children,
}: ConfirmInlineProps) {
	const [pending, setPending] = useState(false);
	const confirmBtnRef = useRef<HTMLButtonElement>(null);

	// Pull keyboard focus onto the confirm button when the prompt arms. The
	// trigger that opened it has just unmounted, so without this focus drops to
	// <body> and the destructive action is unreachable by keyboard. Mirrors
	// ToolkitKillSwitch.
	useEffect(() => {
		if (pending) confirmBtnRef.current?.focus();
	}, [pending]);

	if (!pending) {
		const childOnClick = (children.props as { onClick?: (e: React.MouseEvent) => void })
			.onClick;
		return React.cloneElement(children, {
			onClick: (e: React.MouseEvent) => {
				// Preserve the child's own handler before arming the confirm so we
				// don't silently swallow it (latent footgun on a cloned trigger).
				childOnClick?.(e);
				e.stopPropagation();
				setPending(true);
			},
		} as Partial<typeof children.props>);
	}

	return (
		<motion.div
			role="group"
			aria-label={message}
			initial={{ opacity: 0, x: 6 }}
			animate={{ opacity: 1, x: 0 }}
			transition={{ duration: 0.15, ease: 'easeOut' }}
			className="flex flex-wrap items-center justify-end gap-x-2 gap-y-1.5"
			onClick={(e) => e.stopPropagation()}
			onKeyDown={(e) => {
				if (e.key === 'Escape') {
					e.stopPropagation();
					setPending(false);
				}
			}}
		>
			<span className="text-muted-foreground text-xs" aria-live="polite">
				{message}
			</span>
			<Button
				ref={confirmBtnRef}
				size="sm"
				variant={variant === 'danger' ? 'danger' : 'primary'}
				onClick={() => {
					onConfirm();
					setPending(false);
				}}
			>
				{confirmLabel}
			</Button>
			<Button size="sm" variant="ghost" onClick={() => setPending(false)}>
				Cancel
			</Button>
		</motion.div>
	);
}
