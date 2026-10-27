/**
 * PlainText — what an action does, in plain English, on one quiet line. Text
 * the API gave is shown as is; a phrase described from the method and path is
 * set in italics and says so on hover, so it's never mistaken for the API's
 * own wording. Truncates rather than disappears in a narrow pane.
 */
import { cn } from '@/shared/lib/utils';

export function PlainText({
	value,
	className,
}: {
	value: { text: string; generated: boolean } | undefined;
	className?: string;
}) {
	if (!value) return null;
	return (
		<span
			className={cn(
				'text-muted-foreground min-w-0 truncate text-xs',
				value.generated && 'italic',
				className,
			)}
			title={
				value.generated ? `${value.text} — described from its method and path` : value.text
			}
			data-generated={value.generated || undefined}
		>
			{value.text}
		</span>
	);
}
