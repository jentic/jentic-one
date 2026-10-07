/**
 * LogLayout — the Activity log beside its open record.
 *
 * From xl the record docks in a sticky pane to the right of the list, and the
 * list narrows to make room (its rows reflow — see LogList). j/k step the
 * pane through the list and Esc closes it. Below xl the same record opens in
 * a modal sheet instead. Either way the record lives in the URL (see
 * lib/useLogDetail), so Back closes it and a link can land on it.
 *
 * DetailFrame is the chrome every record shares: an eyebrow naming the kind,
 * a plain-language heading, the raw id with a copy button, and the record's
 * actions in the footer.
 */
import { useId, useRef, type ReactNode } from 'react';
import { X } from 'lucide-react';
import {
	Button,
	CopyButton,
	Kbd,
	SheetBody,
	SheetFooter,
	SheetHeader,
	SheetPrimitive,
} from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import type { LogDetail } from '@/modules/monitor/lib/useLogDetail';
import { useLogStepping } from '@/modules/monitor/components/LogList';
import { DetailRow } from '@/modules/monitor/components/Detail';

/** Where a record is rendering — the docked pane or the modal sheet. */
export interface DetailFrameContext {
	mode: 'pane' | 'sheet';
	headingId: string;
	onClose: () => void;
}

const LIST_VT = { viewTransitionName: 'log-list' } as const;
const PANE_VT = { viewTransitionName: 'log-detail' } as const;

export function LogLayout({
	detail,
	docked,
	onClose,
	renderDetail,
	children,
}: {
	detail: LogDetail | null;
	docked: boolean;
	onClose: () => void;
	renderDetail: (detail: LogDetail, frame: DetailFrameContext) => ReactNode;
	children: ReactNode;
}) {
	const headingId = useId();
	const listRef = useRef<HTMLDivElement>(null);
	// The sheet animates out after the URL has already dropped the record;
	// keep rendering the last one so it doesn't empty mid-slide.
	const lastDetail = useRef<LogDetail | null>(null);
	if (detail) lastDetail.current = detail;
	const shown = detail ?? lastDetail.current;
	const paneOpen = docked && detail != null;

	useLogStepping(listRef, { enabled: paneOpen, onClose });

	return (
		<div
			className={cn(
				'grid grid-cols-1 items-start gap-4',
				paneOpen && 'xl:grid-cols-[minmax(0,1fr)_26rem]',
			)}
		>
			<div ref={listRef} className="min-w-0" style={LIST_VT}>
				{children}
			</div>

			{paneOpen && detail && (
				<aside
					aria-labelledby={headingId}
					className="bg-surface-1 sticky top-[calc(var(--log-top,0px)+1rem)] flex h-[calc(100dvh-3rem-var(--log-top,0px)-2rem)] min-h-0 flex-col overflow-hidden rounded-lg [--field-bg:var(--surface-field)]"
					style={PANE_VT}
				>
					{renderDetail(detail, { mode: 'pane', headingId, onClose })}
				</aside>
			)}

			{!docked && (
				<SheetPrimitive open={detail != null} onClose={onClose} ariaLabelledBy={headingId}>
					{shown && renderDetail(shown, { mode: 'sheet', headingId, onClose })}
				</SheetPrimitive>
			)}
		</div>
	);
}

export function DetailFrame({
	frame,
	eyebrow,
	heading,
	id,
	idLabel,
	actions,
	children,
}: {
	frame: DetailFrameContext;
	/** The record's kind: "Trace", "Execution", "Job", "Audit entry". */
	eyebrow: string;
	/** Plain-language headline; the raw id sits under it. */
	heading: ReactNode;
	id: string;
	/** Names the id for the copy button ("trace id"). */
	idLabel: string;
	/** Footer actions (Cancel job). */
	actions?: ReactNode;
	children: ReactNode;
}) {
	const pane = frame.mode === 'pane';
	return (
		<div className="flex h-full min-h-0 flex-col">
			<SheetHeader className="gap-2">
				<div className="min-w-0 flex-1">
					<p className="text-foreground-faint text-[10.5px] font-bold tracking-[0.08em] uppercase">
						{eyebrow}
					</p>
					<h2
						id={frame.headingId}
						className="font-heading text-foreground-name mt-1 text-base leading-snug font-semibold break-words"
					>
						{heading}
					</h2>
					<div className="text-muted-foreground mt-1 flex min-w-0 items-center gap-1">
						<span className="truncate font-mono text-xs">{id}</span>
						<CopyButton
							value={id}
							size="icon"
							variant="ghost"
							className="h-6 w-6 shrink-0"
							ariaLabel={`Copy ${idLabel}`}
							toastMessage={`Copied ${idLabel}`}
						/>
					</div>
				</div>
				{pane && (
					<Button
						variant="ghost"
						size="icon-xs"
						className="-mr-1.5 shrink-0"
						onClick={frame.onClose}
						aria-label="Close details"
					>
						<X className="h-4 w-4" aria-hidden="true" />
					</Button>
				)}
			</SheetHeader>

			<SheetBody className="space-y-5">{children}</SheetBody>

			{pane ? (
				<SheetFooter className="text-muted-foreground justify-start gap-2 py-2.5 text-xs">
					{actions}
					<span className="ml-auto inline-flex items-center gap-1">
						<Kbd>J</Kbd>
						<Kbd>K</Kbd>
						<span className="mr-2">step</span>
						<Kbd>Esc</Kbd>
						<span>close</span>
					</span>
				</SheetFooter>
			) : (
				<SheetFooter className="gap-2">
					{actions}
					<Button variant="outline" onClick={frame.onClose} className="flex-1">
						Close
					</Button>
				</SheetFooter>
			)}
		</div>
	);
}

/** A label / id row with a copy button — how raw ids surface in a record. */
export function IdRow({ label, value }: { label: string; value: string }) {
	return (
		<DetailRow
			label={label}
			value={
				<span className="inline-flex max-w-full items-center justify-end gap-0.5">
					<span className="min-w-0 truncate font-mono text-xs">{value}</span>
					<CopyButton
						value={value}
						size="icon"
						variant="ghost"
						className="h-6 w-6 shrink-0"
						ariaLabel={`Copy ${label.toLowerCase()}`}
						toastMessage={`Copied ${label.toLowerCase()}`}
					/>
				</span>
			}
		/>
	);
}
