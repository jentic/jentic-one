import { Copy, Check } from 'lucide-react';
import { Button } from '@/shared/ui/Button';
import { toast } from '@/shared/ui/toastStore';
import { useCopyToClipboard } from '@/shared/lib/useCopyToClipboard';
import { cn } from '@/shared/lib/utils';

interface CopyButtonProps {
	value: string;
	/**
	 * Visible text rendered next to the icon. Omit for an icon-only
	 * button — see `ariaLabel` to customise the accessible name in
	 * that case.
	 */
	label?: string;
	/**
	 * Override the button's accessible name. Only used when `label`
	 * is omitted (icon-only mode). Defaults to "Copy to clipboard".
	 */
	ariaLabel?: string;
	className?: string;
	/**
	 * Custom toast message shown after a successful copy. Set to `false`
	 * to opt out of the toast entirely. Defaults to "Copied to clipboard".
	 */
	toastMessage?: string | false;
	/** Visual size — matches the underlying `Button` size token. Defaults to `sm`. */
	size?: 'sm' | 'icon';
	/** Visual variant — matches the underlying `Button` variant. Defaults to `secondary`. */
	variant?: 'secondary' | 'ghost';
}

/**
 * Reusable "copy to clipboard" affordance. Renders an inline
 * check/clipboard icon (with optional trailing label) and fires a
 * transient toast on success.
 */
export function CopyButton({
	value,
	label,
	ariaLabel,
	className,
	toastMessage = 'Copied to clipboard',
	size = 'sm',
	variant = 'secondary',
}: CopyButtonProps) {
	const { copied, copy } = useCopyToClipboard();

	const handleClick = async () => {
		try {
			await copy(value);
			if (toastMessage !== false) {
				// Stable id so rapid double-clicks just refresh the toast
				// instead of stacking three of them on the screen.
				toast({
					id: 'copy-to-clipboard',
					title: toastMessage,
					variant: 'success',
					durationMs: 2000,
				});
			}
		} catch {
			toast({
				id: 'copy-to-clipboard',
				title: 'Could not copy to clipboard',
				variant: 'error',
			});
		}
	};

	return (
		<Button
			type="button"
			variant={variant}
			size={size}
			onClick={() => void handleClick()}
			className={cn('shrink-0', className)}
			aria-label={label ? undefined : (ariaLabel ?? 'Copy to clipboard')}
		>
			{copied ? (
				<>
					<Check className="text-success h-4 w-4" />
					{label ? 'Copied!' : null}
				</>
			) : (
				<>
					<Copy className="h-4 w-4" />
					{label ?? null}
				</>
			)}
		</Button>
	);
}
