/**
 * VendorIcon — deterministic pastel initials mark for an API vendor.
 *
 * A flat tile from the shared pastel palette (`avatarPalette`), seeded by the
 * vendor key so the same vendor always renders the same colour, with dark
 * same-hue initials set in the heading face (Sora 700). No gradient, shadow
 * or ring — the mark sits calmly beside the name rather than competing with
 * it. When an `iconUrl` is known (Workspace's `icon_url`) the real logo is
 * rendered instead, at the same size and radius.
 *
 * Shared by every module that shows an API identity (catalog ledger, the
 * workspace sidebar, the preview sheet, hub headers, agent tiles, Monitor).
 */
import { cn } from '@/shared/lib/utils';
import { avatarToneIndex, avatarToneStyle } from '@/shared/ui/avatarPalette';

function getInitials(name: string): string {
	return (
		name
			.replace(/[^a-z0-9]/gi, '')
			.slice(0, 2)
			.toUpperCase() || '??'
	);
}

type IconSize = 'xs' | 'sm' | 'md' | 'lg';

/** 24 (ledger rows) · 28 (sidebar rows, drag ghost) · 36 (cards) · 44 (sheet/hub headers). */
const SIZE: Record<IconSize, { box: string; radius: string; text: string }> = {
	xs: { box: 'h-6 w-6', radius: 'rounded-[7px]', text: 'text-[9.5px]' },
	sm: { box: 'h-7 w-7', radius: 'rounded-[7px]', text: 'text-[10.5px]' },
	md: { box: 'h-9 w-9', radius: 'rounded-field', text: 'text-xs' },
	lg: { box: 'h-11 w-11', radius: 'rounded-[11px]', text: 'text-sm' },
};

export interface VendorIconProps {
	/** Human-readable name used for the initials. */
	name: string;
	/** Vendor / domain key used to seed the colour (falls back to `name`). */
	vendor?: string;
	/** When present, render the real logo instead of the initials tile. */
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

	const tone = avatarToneIndex(vendor ?? name);
	return (
		<div
			data-tone={tone}
			data-testid="vendor-mark"
			className={cn(
				'font-heading grid shrink-0 place-items-center leading-none font-bold tracking-[0.01em] uppercase select-none',
				box,
				radius,
				text,
				className,
			)}
			style={avatarToneStyle(tone)}
			aria-hidden="true"
		>
			{getInitials(name)}
		</div>
	);
}
