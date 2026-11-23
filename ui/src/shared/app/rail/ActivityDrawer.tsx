/**
 * ActivityDrawerButton — the rail for viewports below `xl`, where the docked
 * rail is hidden. A top-bar button (with a red dot for recent failures —
 * counts are the Notifications bell's job) opens the SAME `ActivityRailBody`,
 * Inbox and Activity tabs included, in a right-side sheet. Below `xl` it also
 * answers `?inbox=1`, opening on the Inbox tab.
 */
import { useEffect, useState } from 'react';
import { Activity } from 'lucide-react';
import { SheetPrimitive } from '@/shared/ui';
import { ActivityRailBody, useScopedActivity } from '@/shared/app/rail/ActivityRailBody';
import { useRailView } from '@/shared/app/rail/railPreferences';
import { RAIL_DOCKED_QUERY, useInboxParam } from '@/shared/app/rail/useInboxParam';
import { cn } from '@/shared/lib/utils';

export function ActivityDrawerButton({ className }: { className?: string }) {
	const [open, setOpen] = useState(false);
	const { failureCount } = useScopedActivity();
	const { setTab } = useRailView();
	useInboxParam('drawer', () => {
		setTab('inbox');
		setOpen(true);
	});

	// Growing past `xl` hides this button and shows the docked rail — don't
	// leave an orphaned sheet over it.
	useEffect(() => {
		if (!open) return undefined;
		const mq = window.matchMedia(RAIL_DOCKED_QUERY);
		const onChange = () => mq.matches && setOpen(false);
		mq.addEventListener('change', onChange);
		return () => mq.removeEventListener('change', onChange);
	}, [open]);

	const label =
		failureCount > 0
			? `Activity (${failureCount} recent failure${failureCount === 1 ? '' : 's'})`
			: 'Activity';

	return (
		<>
			<button
				type="button"
				onClick={() => setOpen(true)}
				aria-label={label}
				aria-haspopup="dialog"
				aria-expanded={open}
				title={label}
				className={cn(
					'text-muted-foreground hover:bg-surface-tonal hover:text-foreground relative flex h-7 shrink-0 items-center gap-1.5 rounded-md px-2 text-sm font-medium transition-colors duration-150',
					className,
				)}
			>
				<Activity className="h-4 w-4 shrink-0" aria-hidden="true" />
				<span className="hidden md:inline">Activity</span>
				{failureCount > 0 && (
					<span className="bg-danger ring-background absolute top-0.5 left-5 h-2 w-2 rounded-full ring-2" />
				)}
			</button>
			<SheetPrimitive
				open={open}
				onClose={() => setOpen(false)}
				ariaLabel="Activity"
				className="bg-surface-sheet flex flex-col sm:w-[360px]"
			>
				<ActivityRailBody
					variant="drawer"
					onCollapse={() => setOpen(false)}
					onNavigated={() => setOpen(false)}
				/>
			</SheetPrimitive>
		</>
	);
}
