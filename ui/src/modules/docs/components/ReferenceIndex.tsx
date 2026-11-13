import { memo, useEffect, useRef } from 'react';
import type { ParsedSpec } from '@/modules/docs/lib/apiSpec';
import { scrollToAnchor } from '@/modules/docs/lib/anchor';
import {
	modelsAnchorId,
	opAnchorId,
	prefixedModelAnchorId,
	tagAnchorId,
	tagGroupAnchorId,
} from '@/modules/docs/components/apiReferenceAnchors';
import { MethodBadge } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';

function jumpTo(id: string) {
	scrollToAnchor(id);
}

/**
 * A single operation row in the left index. Memoized so a scroll-spy `activeId`
 * change re-renders only the two rows whose `active` flips, not all ~hundreds
 * of rows — the index is the dominant render cost on large specs.
 */
const IndexOpRow = memo(function IndexOpRow({
	id,
	method,
	path,
	active,
}: {
	id: string;
	method: string;
	path: string;
	active: boolean;
}) {
	return (
		<li>
			<button
				type="button"
				data-index-for={id}
				aria-current={active ? 'location' : undefined}
				onClick={() => jumpTo(id)}
				className={cn(
					'flex w-full items-center gap-1.5 rounded px-2 py-1 text-left transition-colors',
					active ? 'bg-primary/10' : 'hover:bg-muted',
				)}
				title={`${method} ${path}`}
			>
				<MethodBadge method={method} />
				<code
					className={cn(
						'truncate font-mono text-[12px]',
						active ? 'text-primary' : 'text-foreground/65',
					)}
				>
					{path}
				</code>
			</button>
		</li>
	);
});

/** A single model row in the left index. Memoized for the same reason as IndexOpRow. */
const IndexModelRow = memo(function IndexModelRow({
	id,
	name,
	active,
}: {
	id: string;
	name: string;
	active: boolean;
}) {
	return (
		<li>
			<button
				type="button"
				data-index-for={id}
				aria-current={active ? 'location' : undefined}
				onClick={() => jumpTo(id)}
				className={cn(
					'hover:bg-muted hover:text-foreground block w-full truncate rounded px-2 py-1 text-left font-mono text-[12px]',
					active ? 'bg-primary/10 text-primary' : 'text-foreground-sub',
				)}
			>
				{name}
			</button>
		</li>
	);
});

export function ReferenceIndex({
	parsed,
	activeId,
	showModels,
	prefix = '',
}: {
	parsed: ParsedSpec;
	activeId: string | null;
	showModels: boolean;
	prefix?: string;
}) {
	const navRef = useRef<HTMLElement | null>(null);

	// Keep the highlighted operation in view as the reader scrolls the document
	// (and after a click) — the long index otherwise leaves the active entry
	// off-screen. Mirrors the CLI command index.
	useEffect(() => {
		if (!activeId) return;
		const nav = navRef.current;
		if (!nav) return;
		const el = nav.querySelector<HTMLElement>(`[data-index-for="${CSS.escape(activeId)}"]`);
		if (!el) return;
		const navBox = nav.getBoundingClientRect();
		const elBox = el.getBoundingClientRect();
		if (elBox.top < navBox.top || elBox.bottom > navBox.bottom) {
			el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
		}
	}, [activeId]);

	return (
		<nav
			ref={navRef}
			aria-label="API operations"
			className="hidden lg:sticky lg:top-24 lg:block lg:max-h-[calc(100vh-7rem)] lg:self-start lg:overflow-y-auto lg:pr-1"
		>
			<div className="space-y-5">
				{parsed.groups.map((group) => (
					<div key={group.name}>
						<button
							type="button"
							onClick={() => jumpTo(tagGroupAnchorId(group.name, prefix))}
							className="text-foreground-faint hover:text-foreground mb-1 px-2 text-left text-[10px] font-semibold tracking-wider uppercase"
						>
							{group.name}
						</button>
						{group.tags.map((tag) => (
							<div key={tag.name} className="mb-2">
								<button
									type="button"
									onClick={() => jumpTo(tagAnchorId(tag.name, prefix))}
									className="text-foreground/70 hover:text-foreground mb-0.5 px-2 text-left text-[11px] font-medium"
								>
									{tag.name}
								</button>
								<ul className="space-y-0.5">
									{tag.operations.map((op) => {
										const id = opAnchorId(op, prefix);
										return (
											<IndexOpRow
												key={id}
												id={id}
												method={op.method}
												path={op.path}
												active={id === activeId}
											/>
										);
									})}
								</ul>
							</div>
						))}
					</div>
				))}

				{showModels && (
					<div>
						<button
							type="button"
							onClick={() => jumpTo(modelsAnchorId(prefix))}
							className="text-foreground-faint hover:text-foreground mb-1 px-2 text-left text-[10px] font-semibold tracking-wider uppercase"
						>
							Models
						</button>
						<ul className="space-y-0.5">
							{parsed.models.map((m) => {
								const id = prefixedModelAnchorId(m.name, prefix);
								return (
									<IndexModelRow
										key={m.name}
										id={id}
										name={m.name}
										active={id === activeId}
									/>
								);
							})}
						</ul>
					</div>
				)}
			</div>
		</nav>
	);
}
