/**
 * UserText — the wrapper every USER-SUPPLIED string is rendered inside.
 *
 * Unicode carries directional formatting characters (U+202A–U+202E
 * LRE/RLE/PDF/LRO/RLO, U+2066–U+2069) whose effect runs to the end of the
 * enclosing bidi paragraph, not to the end of the string that contained them.
 * A name such as `invoice\u202ebot` therefore reverses the COPY AROUND IT: the
 * agents grid rendered "Agent can reach nothing yet" backwards because the
 * agent's name and that sentence share one element. The same applies to any
 * right-to-left text, which flips neighbouring punctuation without any control
 * character at all.
 *
 * The fix is bidi ISOLATION, not sanitisation: stripping characters would
 * mangle legitimate Arabic/Hebrew names and still miss the next control
 * character Unicode adds. `<bdi>` is exactly this — `unicode-bidi: isolate`
 * plus `dir="auto"` by UA default — so the content is resolved as its own
 * directional run and cannot reach outside. It leaves `textContent` untouched
 * (unlike wrapping in U+2068/U+2069), so names stay copyable and queryable.
 *
 * Use it where a chosen string sits INSIDE other copy — `{name} can reach
 * nothing yet`, `… — access for {name}` — because that is the case where the
 * name and the product copy would otherwise share one bidi paragraph.
 *
 * Where the string ALREADY has an element of its own (`<h3>{api.name}</h3>`),
 * do not nest a `<bdi>` inside it: put `dir="auto"` on that element instead.
 * The HTML rendering spec gives every `[dir]` element `unicode-bidi: isolate`,
 * so the isolation is identical while the DOM — and anything reading its
 * text — stays exactly as it was.
 *
 * For the places that can only take a string — `aria-label`, `title`, a toast
 * title, `document.title` — use `isolateText` from `@/shared/lib/utils`, which
 * does the same job with the Unicode isolate characters. Purely visually hidden
 * copy (`sr-only`) needs neither: it is never laid out, and splitting it only
 * complicates the accessible name.
 */
import type { ReactNode } from 'react';
import { cn } from '@/shared/lib/utils';

export interface UserTextProps {
	children: ReactNode;
	className?: string;
	/** Forwarded so a host can keep its own truncation/title behaviour. */
	title?: string;
}

export function UserText({ children, className, title }: UserTextProps) {
	return (
		// `dir="auto"` is the UA default for `<bdi>`, but stated explicitly so a
		// global `dir` or a reset stylesheet cannot quietly turn the isolation off.
		<bdi dir="auto" className={cn(className)} title={title}>
			{children}
		</bdi>
	);
}
