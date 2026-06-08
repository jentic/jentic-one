import React, { useState } from 'react';
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

	if (!pending) {
		return React.cloneElement(children, {
			onClick: (e: React.MouseEvent) => {
				e.stopPropagation();
				setPending(true);
			},
		});
	}

	return (
		<motion.div
			initial={{ opacity: 0, x: 6 }}
			animate={{ opacity: 1, x: 0 }}
			transition={{ duration: 0.15, ease: 'easeOut' }}
			className="flex flex-wrap items-center justify-end gap-x-2 gap-y-1.5"
			onClick={(e) => e.stopPropagation()}
		>
			<span className="text-muted-foreground text-xs">{message}</span>
			<Button
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
