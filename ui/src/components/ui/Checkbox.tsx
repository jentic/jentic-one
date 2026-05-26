/* eslint-disable no-restricted-syntax */
import type { JSX, ReactNode } from 'react';
import { Check } from 'lucide-react';
import { cn } from '@/lib/utils';

type CheckboxSize = 'sm' | 'md' | 'lg';

interface CheckboxProps {
	checked: boolean;
	onChange: (checked: boolean) => void;
	disabled?: boolean;
	size?: CheckboxSize;
	className?: string;
	ariaLabel?: string;
	children?: ReactNode;
	id?: string;
}

const SIZE_CLASSES: Record<CheckboxSize, { box: string; icon: string }> = {
	sm: { box: 'h-4 w-4', icon: 'h-3 w-3' },
	md: { box: 'h-5 w-5', icon: 'h-3.5 w-3.5' },
	lg: { box: 'h-6 w-6', icon: 'h-4 w-4' },
};

export function Checkbox({
	checked,
	onChange,
	disabled = false,
	size = 'md',
	className,
	ariaLabel,
	children,
}: CheckboxProps): JSX.Element {
	const sizeClasses = SIZE_CLASSES[size];

	const checkboxBox = (
		<span
			className={cn(
				'flex items-center justify-center rounded border transition-colors',
				sizeClasses.box,
				checked ? 'border-primary bg-primary' : 'border-2',
				!checked && !disabled && 'border-border',
				!checked && disabled && 'border-muted',
				!checked && !disabled && 'group-hover:border-foreground/50',
				disabled && 'opacity-40',
			)}
		>
			{checked && <Check className={cn(sizeClasses.icon, 'text-primary-foreground')} />}
		</span>
	);

	if (!children) {
		return (
			<button
				type="button"
				role="checkbox"
				aria-checked={checked}
				aria-label={ariaLabel || 'Select item'}
				onClick={(e) => {
					e.stopPropagation();
					if (!disabled) onChange(!checked);
				}}
				disabled={disabled}
				className={cn(
					'group flex items-center justify-center rounded p-1 transition-all',
					disabled ? 'cursor-not-allowed' : 'cursor-pointer',
					className,
				)}
			>
				{checkboxBox}
			</button>
		);
	}

	return (
		<button
			type="button"
			role="checkbox"
			aria-checked={checked}
			aria-label={ariaLabel}
			onClick={(e) => {
				e.stopPropagation();
				if (!disabled) onChange(!checked);
			}}
			disabled={disabled}
			className={cn(
				'group flex items-center gap-3 text-left select-none',
				disabled ? 'cursor-not-allowed' : 'cursor-pointer',
				className,
			)}
		>
			<span className="shrink-0">{checkboxBox}</span>
			<span
				className={cn('text-muted-foreground text-sm leading-5', disabled && 'opacity-50')}
			>
				{children}
			</span>
		</button>
	);
}
