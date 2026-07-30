import { useMemo } from 'react';
import { cn } from '@/shared/lib/utils';

/**
 * Deterministic identity tile for a toolkit — hashes the name to a stable
 * gradient + initials so each toolkit reads with a consistent, colourful
 * identity everywhere it appears (toolkit list cards, the agent console's
 * bound-toolkit rows, pickers). Lives in `shared/` so sibling modules can
 * render a toolkit identically without importing each other
 * (module-boundary rule).
 */
const GRADIENTS = [
	'from-indigo-500 to-violet-500',
	'from-sky-500 to-cyan-500',
	'from-emerald-500 to-teal-500',
	'from-amber-500 to-orange-500',
	'from-rose-500 to-pink-500',
	'from-fuchsia-500 to-purple-500',
];

const SIZES = {
	sm: 'h-8 w-8 text-[11px]',
	md: 'h-10 w-10 text-xs',
	lg: 'h-12 w-12 text-sm',
} as const;

function initials(name: string): string {
	const parts = name.trim().split(/\s+/).filter(Boolean);
	if (parts.length === 0) return '?';
	if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
	return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function hash(str: string): number {
	let h = 0;
	for (let i = 0; i < str.length; i += 1) {
		h = (h << 5) - h + str.charCodeAt(i);
		h |= 0;
	}
	return Math.abs(h);
}

export interface ToolkitGlyphProps {
	name: string;
	size?: keyof typeof SIZES;
	className?: string;
}

export function ToolkitGlyph({ name, size = 'md', className }: ToolkitGlyphProps) {
	const gradient = useMemo(() => GRADIENTS[hash(name) % GRADIENTS.length], [name]);
	return (
		<span
			aria-hidden="true"
			className={cn(
				'inline-flex shrink-0 items-center justify-center rounded-xl bg-gradient-to-br font-semibold text-white',
				gradient,
				SIZES[size],
				className,
			)}
		>
			{initials(name)}
		</span>
	);
}
