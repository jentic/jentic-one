import React, { useId } from 'react';
import { cn } from '@/shared/lib/utils';

type Resize = 'none' | 'vertical' | 'horizontal' | 'both';

const resizeClasses: Record<Resize, string> = {
	none: 'resize-none',
	vertical: 'resize-y',
	horizontal: 'resize-x',
	both: 'resize',
};

type TextareaProps = React.ComponentProps<'textarea'> & {
	error?: string;
	resizable?: Resize;
};

export const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
	{ error, resizable = 'vertical', className, id, ...props },
	ref,
) {
	const generatedId = useId();
	const textareaId = id ?? generatedId;
	const errorId = error ? `${textareaId}-error` : undefined;

	return (
		<div className="w-full">
			<textarea
				ref={ref}
				id={textareaId}
				aria-describedby={errorId}
				aria-invalid={error ? true : undefined}
				className={cn(
					'bg-card border-border text-foreground placeholder:text-muted-foreground w-full rounded-lg border px-4 py-2 text-sm transition-colors',
					'focus:border-primary focus:outline-hidden',
					resizeClasses[resizable],
					error && 'border-danger focus:border-danger',
					className,
				)}
				{...props}
			/>
			{error && (
				<p id={errorId} className="text-danger mt-1 text-xs" role="alert">
					{error}
				</p>
			)}
		</div>
	);
});

Textarea.displayName = 'Textarea';

export type { TextareaProps };
