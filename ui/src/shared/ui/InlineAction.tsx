/**
 * InlineAction — a quiet action that sits inside a line of text (a row's
 * "Resume", a status that opens what fixes it). It is a ghost `Button` sized
 * to the text around it: a compact pill whose 3px block padding (and whatever
 * inline padding the caller gives it) is cancelled by an equal negative
 * margin, so nothing moves between rest and hover. On hover or keyboard focus
 * it takes the ghost fill; the type (size, line height) is the line's own, and
 * the caller sets the weight and colour where the content doesn't carry them.
 * Raised (`relative z-10`) so it sits above a card's stretched overlay.
 */
import React from 'react';
import { Button, type ButtonProps } from '@/shared/ui/Button';
import { cn } from '@/shared/lib/utils';

const INLINE_ACTION =
	'relative z-10 -my-[3px] h-auto min-w-0 justify-start gap-1 rounded-md py-[3px] text-[length:inherit] leading-[inherit] ' +
	'transition-[background-color,color] duration-150 ease-out motion-reduce:transition-none ' +
	'focus-visible:bg-tint-2 hover:bg-tint-2 focus-visible:ring-offset-0 active:scale-100';

export type InlineActionProps = Omit<ButtonProps, 'variant' | 'size' | 'loading'>;

export const InlineAction = React.forwardRef<HTMLButtonElement, InlineActionProps>(
	function InlineAction({ className, ...props }, ref) {
		return (
			<Button
				ref={ref}
				variant="ghost"
				size="xs"
				className={cn(INLINE_ACTION, className)}
				{...props}
			/>
		);
	},
);
