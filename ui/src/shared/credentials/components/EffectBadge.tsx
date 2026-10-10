import type { ComponentProps } from 'react';
import { Badge } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import type { PermissionRule } from '@/shared/credentials/api/vendors-types';

type BadgeProps = ComponentProps<typeof Badge>;

/**
 * Badge's warm variants leave the word untinted and lean on a dot. A rule
 * effect is one of three peers, so Ask gets the same tinted fill and word as
 * allow and deny, in the warning hue.
 */
const EFFECT_BADGE: Record<
	PermissionRule['effect'],
	Pick<BadgeProps, 'variant' | 'dot' | 'className'>
> = {
	allow: { variant: 'success' },
	'require-approval': { variant: 'warning', dot: false, className: 'bg-warning/10 text-warning' },
	deny: { variant: 'danger' },
};

/** The badge props for one effect, merged with the caller's own classes. */
export function effectBadgeProps(
	effect: PermissionRule['effect'],
	className?: string,
): Pick<BadgeProps, 'variant' | 'dot' | 'className'> {
	const style = EFFECT_BADGE[effect];
	return { ...style, className: cn(style.className, className) };
}

/** A permission-rule effect as a badge. */
export function EffectBadge({
	effect,
	className,
	...props
}: { effect: PermissionRule['effect'] } & Omit<BadgeProps, 'variant' | 'dot'>) {
	return <Badge {...props} {...effectBadgeProps(effect, className)} />;
}
