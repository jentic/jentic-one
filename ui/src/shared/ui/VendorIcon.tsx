/**
 * VendorIcon — deterministic gradient + initials avatar for an API vendor.
 *
 * jentic-mini resolves real brand logos from a `vendor-registry` (CDN slugs,
 * brand colours); that registry is mini-specific infra and out of scope here.
 * This is a self-contained gradient+initials fallback seeded by the vendor key,
 * so the same vendor always renders the same colour — enough visual
 * differentiation for the grids with no network dependency. When an `iconUrl`
 * is known (Workspace's `icon_url`) the real logo is rendered instead.
 *
 * Shared by Discover and Workspace (both render vendor avatars in their grids
 * and detail headers).
 */
import { cn } from '@/shared/lib/utils';

const GRADIENTS = [
	'from-blue-500 to-blue-600',
	'from-emerald-500 to-emerald-600',
	'from-violet-500 to-violet-600',
	'from-orange-500 to-orange-600',
	'from-pink-500 to-pink-600',
	'from-teal-500 to-teal-600',
	'from-indigo-500 to-indigo-600',
	'from-cyan-500 to-cyan-600',
	'from-rose-500 to-rose-600',
	'from-amber-500 to-amber-600',
] as const;

/** Stable string hash (djb2) — same seed always picks the same gradient. */
function hashStr(input: string): number {
	let hash = 5381;
	for (let i = 0; i < input.length; i += 1) {
		hash = (hash * 33) ^ input.charCodeAt(i);
	}
	return Math.abs(hash);
}

function getInitials(name: string): string {
	return (
		name
			.replace(/[^a-z0-9]/gi, '')
			.slice(0, 2)
			.toUpperCase() || '??'
	);
}

type IconSize = 'sm' | 'md' | 'lg';

const SIZE: Record<IconSize, { box: string; radius: string; text: string }> = {
	sm: { box: 'h-8 w-8', radius: 'rounded-[8px]', text: 'text-[11px]' },
	md: { box: 'h-10 w-10', radius: 'rounded-[10px]', text: 'text-sm' },
	lg: { box: 'h-12 w-12', radius: 'rounded-xl', text: 'text-base' },
};

export interface VendorIconProps {
	/** Human-readable name used for the initials. */
	name: string;
	/** Vendor / domain key used to seed the gradient (falls back to `name`). */
	vendor?: string;
	/** When present, render the real logo instead of the gradient fallback. */
	iconUrl?: string | null;
	size?: IconSize;
	className?: string;
}

export function VendorIcon({ name, vendor, iconUrl, size = 'md', className }: VendorIconProps) {
	const { box, radius, text } = SIZE[size];

	if (iconUrl) {
		return (
			<img
				src={iconUrl}
				alt=""
				aria-hidden="true"
				className={cn('shrink-0 object-cover', box, radius, className)}
			/>
		);
	}

	const gradient = GRADIENTS[hashStr(vendor ?? name) % GRADIENTS.length];
	return (
		<div
			className={cn(
				'flex shrink-0 items-center justify-center bg-gradient-to-br font-semibold text-white shadow-sm',
				gradient,
				box,
				radius,
				text,
				className,
			)}
			aria-hidden="true"
		>
			{getInitials(name)}
		</div>
	);
}
