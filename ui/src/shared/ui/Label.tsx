import React from 'react';
import { cn } from '@/shared/lib/utils';

type LabelProps = React.ComponentProps<'label'> & {
	required?: boolean;
};

export function Label({ required, children, className, ...props }: LabelProps) {
	return (
		<label
			className={cn(
				'text-foreground text-sm font-medium',
				required && "after:text-danger after:ml-1 after:content-['*']",
				className,
			)}
			{...props}
		>
			{children}
		</label>
	);
}
