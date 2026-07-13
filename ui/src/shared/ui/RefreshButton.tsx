import { RefreshCw } from 'lucide-react';
import { useCallback, useState } from 'react';
import { motion } from 'framer-motion';
import { Button } from '@/shared/ui/Button';
import { cn } from '@/shared/lib/utils';

interface RefreshButtonProps {
	onRefresh: () => void;
	className?: string;
	iconClassName?: string;
	title?: string;
	/**
	 * External "I'm busy" signal — drives the spinner in addition to the
	 * built-in 600ms animation. Use when the refresh kicks off an async
	 * mutation and you want the button to keep spinning until it resolves.
	 */
	pending?: boolean;
	/** Hard-disable the button (e.g. while another mutation is running). */
	disabled?: boolean;
	/** Optional `data-testid` for tests. */
	testId?: string;
}

/**
 * Ghost-styled icon button that spins its `RefreshCw` glyph for ~600ms
 * after each click — visual ack that "your click registered" even when
 * the underlying refresh is too fast to notice.
 */
export function RefreshButton({
	onRefresh,
	className,
	iconClassName = 'h-4 w-4',
	title = 'Refresh',
	pending = false,
	disabled = false,
	testId,
}: RefreshButtonProps) {
	const [spinning, setSpinning] = useState(false);

	const handleRefresh = useCallback(() => {
		if (spinning || pending || disabled) return;
		setSpinning(true);
		onRefresh();
		setTimeout(() => setSpinning(false), 600);
	}, [spinning, pending, disabled, onRefresh]);

	const isAnimating = spinning || pending;

	return (
		<Button
			type="button"
			variant="ghost"
			size="icon"
			onClick={handleRefresh}
			className={cn('hover:bg-muted/50 shrink-0 cursor-pointer', className)}
			disabled={isAnimating || disabled}
			title={title}
			aria-label={title}
			data-testid={testId}
		>
			<motion.div
				animate={isAnimating ? { rotate: 360 } : { rotate: 0 }}
				transition={
					isAnimating
						? { duration: 0.6, ease: 'easeInOut', repeat: pending ? Infinity : 0 }
						: { duration: 0 }
				}
			>
				<RefreshCw
					className={cn('text-muted-foreground hover:text-foreground', iconClassName)}
				/>
			</motion.div>
		</Button>
	);
}
