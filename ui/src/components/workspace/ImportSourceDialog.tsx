import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { AnimatePresence, motion } from 'framer-motion';
import {
	AlertTriangle,
	Boxes,
	ClipboardPaste,
	FileText,
	Globe,
	Loader2,
	Trash2,
	Upload,
	Workflow,
} from 'lucide-react';
import { api } from '@/api/client';
import { Button } from '@/components/ui/Button';
import { Dialog } from '@/components/ui/Dialog';
import { Input } from '@/components/ui/Input';
import { OptionCardSelector } from '@/components/ui/OptionCardSelector';
import { Textarea } from '@/components/ui/Textarea';
import { toast } from '@/components/ui/toastStore';
import { cn } from '@/lib/utils';

/**
 * Single-source import dialog for the Workspace page.
 *
 * Two pivots in one focused surface — both rendered with the
 * shared `OptionCardSelector` primitive so the visual language
 * matches the webapp's "Choose Authentication Method" picker:
 *
 *  1. **Kind** — `API spec` vs `Workflow`. Server-side, `POST
 *     /import` auto-detects OpenAPI vs Arazzo, so the selector
 *     is a *UX* signal (placeholder copy, hint, success label).
 *
 *  2. **Method** — `URL` / `Paste` / `Upload`. Each swaps the
 *     single input area below.
 *
 * The Upload card doubles as a dropzone — drag-and-drop a `.json`,
 * `.yaml`, or `.yml` file anywhere onto the dialog and it lands
 * in the upload state regardless of which method card was selected.
 *
 * Layout choices:
 *
 *  - No "labels above inputs" — the selector cards act as the
 *    label, the input below is the value.
 *  - Footer is a single, full-width primary action. Cancel is
 *    implicit via Esc and the X — there's no second button
 *    competing with the primary.
 *  - Backdrop click does NOT close (`dismissOnBackdrop={false}`)
 *    so a misclick while pasting a long spec doesn't lose state.
 *  - Errors render as an inline strip under the input rather
 *    than a banner.
 *
 * On success: toast, invalidate `['workspace']` + `['workspace-stats']`
 * so the new tile materialises immediately, then close. On per-source
 * error: stay open with the inline strip filled in.
 */

export type ImportTab = 'api' | 'workflow';
type InputMode = 'url' | 'paste' | 'file';

export interface ImportSourceDialogProps {
	open: boolean;
	onClose: () => void;
	defaultTab?: ImportTab;
}

const KIND_COPY: Record<
	ImportTab,
	{
		urlPlaceholder: string;
		pastePlaceholder: string;
		successLabel: string;
		submitLabel: string;
	}
> = {
	api: {
		urlPlaceholder: 'https://example.com/openapi.json',
		pastePlaceholder:
			'{\n  "openapi": "3.1.0",\n  "info": { "title": "My API", … },\n  "paths": { … }\n}',
		successLabel: 'API imported',
		submitLabel: 'Import API',
	},
	workflow: {
		urlPlaceholder: 'https://example.com/checkout.arazzo.json',
		pastePlaceholder:
			'{\n  "arazzo": "1.0.1",\n  "info": { "title": "Checkout", … },\n  "workflows": [ … ]\n}',
		successLabel: 'Workflow imported',
		submitLabel: 'Import workflow',
	},
};

const ACCEPTED_EXTENSIONS = '.json,.yaml,.yml,application/json,application/yaml,text/yaml';

interface ImportResultEntry {
	index?: number;
	status?: string;
	error?: string;
	id?: string;
	slug?: string;
	type?: string;
	[k: string]: unknown;
}

export function ImportSourceDialog({ open, onClose, defaultTab = 'api' }: ImportSourceDialogProps) {
	const queryClient = useQueryClient();
	const fieldId = useId();

	const [tab, setTab] = useState<ImportTab>(defaultTab);
	const [mode, setMode] = useState<InputMode>('url');
	const [urlValue, setUrlValue] = useState('');
	const [pasteValue, setPasteValue] = useState('');
	const [fileName, setFileName] = useState<string | null>(null);
	const [fileSize, setFileSize] = useState<number | null>(null);
	const [fileContent, setFileContent] = useState<string | null>(null);
	const [submitting, setSubmitting] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [dragging, setDragging] = useState(false);

	const fileInputRef = useRef<HTMLInputElement>(null);

	// State lifecycle: reset on **successful submit**, persist between
	// dismissals. See `Dialog`'s JSDoc and the
	// `dialog-state-lifecycle.mdc` Cursor rule for the project-wide rule.
	//
	// We honour `defaultTab` only when its incoming value actually
	// changes (i.e. the parent re-targeted the dialog at a different
	// kind via a different empty-state CTA). We don't sync it on every
	// `open` flip because that would clobber a draft after a casual
	// Esc/X dismissal.
	const lastDefaultTabRef = useRef(defaultTab);
	useEffect(() => {
		if (lastDefaultTabRef.current !== defaultTab) {
			lastDefaultTabRef.current = defaultTab;
			setTab(defaultTab);
		}
	}, [defaultTab]);

	// Always reset the transient flags (submitting / error / dragging)
	// when the dialog re-opens — they aren't user input, they're
	// state from the last attempt and would mislead on the next one
	// (e.g. a stale red error banner above an empty form).
	useEffect(() => {
		if (!open) return;
		setSubmitting(false);
		setError(null);
		setDragging(false);
	}, [open]);

	// Hard reset — only fires after a successful submit (which the
	// parent will follow with `onClose()`). Centralised so the rule is
	// obvious to readers: drafts go away iff the user committed them.
	const resetDraft = useCallback(() => {
		setTab(defaultTab);
		setMode('url');
		setUrlValue('');
		setPasteValue('');
		setFileName(null);
		setFileSize(null);
		setFileContent(null);
		setSubmitting(false);
		setError(null);
		setDragging(false);
		if (fileInputRef.current) fileInputRef.current.value = '';
	}, [defaultTab]);

	const copy = KIND_COPY[tab];

	const kindOptions = useMemo(
		() => [
			{
				value: 'api' as ImportTab,
				label: 'API spec',
				description: 'OpenAPI 3.x — endpoints, schemas, auth.',
				icon: <Boxes className="h-5 w-5" />,
			},
			{
				value: 'workflow' as ImportTab,
				label: 'Workflow',
				description: 'Arazzo 1.0 — orchestrate APIs into multi-step actions.',
				icon: <Workflow className="h-5 w-5" />,
			},
		],
		[],
	);

	const methodOptions = useMemo(
		() => [
			{
				value: 'url' as InputMode,
				label: 'From URL',
				description: 'Fetch a spec from the web.',
				icon: <Globe className="h-5 w-5" />,
			},
			{
				value: 'paste' as InputMode,
				label: 'Paste content',
				description: 'JSON or YAML, inline.',
				icon: <ClipboardPaste className="h-5 w-5" />,
			},
			{
				value: 'file' as InputMode,
				label: 'Upload a file',
				description: 'Drop a .json or .yaml.',
				icon: <Upload className="h-5 w-5" />,
			},
		],
		[],
	);

	const canSubmit = useMemo(() => {
		if (submitting) return false;
		if (mode === 'url') return urlValue.trim().length > 0;
		if (mode === 'paste') return pasteValue.trim().length > 0;
		return fileContent !== null && fileContent.length > 0;
	}, [submitting, mode, urlValue, pasteValue, fileContent]);

	const ingestFile = useCallback(async (file: File) => {
		setMode('file');
		setFileName(file.name);
		setFileSize(file.size);
		try {
			const text = await file.text();
			setFileContent(text);
			setError(null);
		} catch {
			setFileContent(null);
			setError('Could not read that file. Try pasting the contents instead.');
		}
	}, []);

	function clearFile() {
		setFileName(null);
		setFileSize(null);
		setFileContent(null);
		if (fileInputRef.current) fileInputRef.current.value = '';
	}

	async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
		const file = e.target.files?.[0];
		if (!file) {
			clearFile();
			return;
		}
		await ingestFile(file);
	}

	function handleDragOver(e: React.DragEvent<HTMLDivElement>) {
		if (e.dataTransfer.types.includes('Files')) {
			e.preventDefault();
			setDragging(true);
		}
	}
	function handleDragLeave(e: React.DragEvent<HTMLDivElement>) {
		if (e.currentTarget === e.target) setDragging(false);
	}
	async function handleDrop(e: React.DragEvent<HTMLDivElement>) {
		e.preventDefault();
		setDragging(false);
		const file = e.dataTransfer.files?.[0];
		if (file) await ingestFile(file);
	}

	async function handleSubmit() {
		setError(null);
		setSubmitting(true);
		try {
			const source: Record<string, unknown> = (() => {
				if (mode === 'url') return { type: 'url', url: urlValue.trim() };
				if (mode === 'paste') return { type: 'inline', content: pasteValue };
				return {
					type: 'inline',
					content: fileContent ?? '',
					...(fileName ? { filename: fileName } : {}),
				};
			})();
			const res = (await api.importSpec([source])) as { results?: ImportResultEntry[] };
			const result = res?.results?.[0];
			if (!result || result.status !== 'success') {
				const msg =
					result?.error ||
					'Import failed. Check that the document is valid OpenAPI or Arazzo.';
				setError(msg);
				return;
			}

			toast({
				variant: 'success',
				title: copy.successLabel,
				description:
					typeof result.id === 'string'
						? result.id
						: typeof result.slug === 'string'
							? result.slug
							: undefined,
			});
			queryClient.invalidateQueries({ queryKey: ['workspace'] });
			queryClient.invalidateQueries({ queryKey: ['workspace-stats'] });
			// Successful submit is the *only* path that wipes the draft —
			// dismissals (Esc, X, parent re-toggles `open`) preserve it.
			resetDraft();
			onClose();
		} catch (err) {
			const message =
				err instanceof Error
					? err.message
					: typeof err === 'string'
						? err
						: 'Network error while importing. Try again.';
			setError(message);
		} finally {
			setSubmitting(false);
		}
	}

	const kindHeadingId = `${fieldId}-kind-heading`;
	const methodHeadingId = `${fieldId}-method-heading`;

	return (
		<Dialog
			open={open}
			onClose={onClose}
			title="Add to your workspace"
			size="lg"
			className="max-w-xl"
			dismissOnBackdrop={false}
			footer={
				<Button
					variant="primary"
					size="md"
					onClick={handleSubmit}
					disabled={!canSubmit}
					loading={submitting}
					fullWidth
					data-testid="import-source-submit"
				>
					{copy.submitLabel}
				</Button>
			}
		>
			{/* Drop target is progressive enhancement on top of fully
			    keyboard-accessible controls inside — the <div> itself
			    isn't interactive in any other sense. */}
			{/* eslint-disable-next-line jsx-a11y/no-static-element-interactions */}
			<div
				className={cn(
					'relative flex flex-col gap-6 transition-colors',
					dragging && 'ring-primary/40 rounded-lg ring-2',
				)}
				data-testid="import-source-dialog"
				onDragOver={handleDragOver}
				onDragLeave={handleDragLeave}
				onDrop={handleDrop}
			>
				{/* Kind selector */}
				<section className="space-y-3">
					<div>
						<h3 id={kindHeadingId} className="text-foreground text-sm font-medium">
							Choose what to add
						</h3>
						<p className="text-muted-foreground mt-0.5 text-xs">
							The server auto-detects the format — JSON or YAML, either works.
						</p>
					</div>
					<OptionCardSelector
						options={kindOptions}
						value={tab}
						onChange={(v) => setTab(v)}
						columns={2}
						ariaLabelledBy={kindHeadingId}
						data-testid="import-source-kind"
					/>
				</section>

				{/* Method selector */}
				<section className="space-y-3">
					<div>
						<h3 id={methodHeadingId} className="text-foreground text-sm font-medium">
							Choose import method
						</h3>
						<p className="text-muted-foreground mt-0.5 text-xs">
							Select how you want to provide the document.
						</p>
					</div>
					<OptionCardSelector
						options={methodOptions}
						value={mode}
						onChange={(v) => setMode(v)}
						columns={3}
						variant="compact"
						ariaLabelledBy={methodHeadingId}
						data-testid="import-source-methods"
					/>
				</section>

				{/* Input area — animates between methods so the swap doesn't feel like a page change. */}
				<AnimatePresence mode="wait" initial={false}>
					<motion.div
						key={mode}
						initial={{ opacity: 0, y: 4 }}
						animate={{ opacity: 1, y: 0 }}
						exit={{ opacity: 0, y: -4 }}
						transition={{ duration: 0.15, ease: 'easeOut' }}
						className="flex flex-col gap-2"
					>
						{mode === 'url' ? (
							<Input
								id={`${fieldId}-url`}
								type="url"
								autoFocus
								value={urlValue}
								onChange={(e) => setUrlValue(e.target.value)}
								placeholder={copy.urlPlaceholder}
								data-testid="import-source-url"
								// `py-3` overrides the primitive's default
								// `py-2` to give the dialog more breathing
								// room; `font-mono` because the value will
								// almost always be a URL or a path.
								className="py-3 font-mono"
							/>
						) : mode === 'paste' ? (
							<Textarea
								id={`${fieldId}-paste`}
								autoFocus
								value={pasteValue}
								onChange={(e) => setPasteValue(e.target.value)}
								placeholder={copy.pastePlaceholder}
								rows={11}
								data-testid="import-source-paste"
								className="py-3 font-mono text-xs leading-relaxed"
							/>
						) : (
							<UploadDropzone
								fileName={fileName}
								fileSize={fileSize}
								dragging={dragging}
								onPick={() => fileInputRef.current?.click()}
								onClear={clearFile}
							/>
						)}
					</motion.div>
				</AnimatePresence>

				<input
					ref={fileInputRef}
					type="file"
					accept={ACCEPTED_EXTENSIONS}
					className="hidden"
					onChange={handleFileChange}
					data-testid="import-source-file-input"
				/>

				{error ? (
					<div
						role="alert"
						className="border-danger/30 bg-danger/5 text-danger flex items-start gap-2 rounded-md border-l-2 px-3 py-2 text-xs"
						data-testid="import-source-error"
					>
						<AlertTriangle size={12} aria-hidden="true" className="mt-0.5 shrink-0" />
						<span className="leading-snug">{error}</span>
					</div>
				) : null}

				{submitting ? (
					<div className="text-muted-foreground inline-flex items-center gap-2 text-xs">
						<Loader2 size={13} className="animate-spin" aria-hidden="true" />
						Importing — this may take a moment for larger specs.
					</div>
				) : null}

				{/* Drop-target overlay — only rendered while a file is being
				    dragged over the dialog. */}
				{dragging ? (
					<div
						className="bg-primary/5 border-primary/40 pointer-events-none absolute inset-0 -m-2 flex items-center justify-center rounded-lg border-2 border-dashed"
						aria-hidden="true"
						data-testid="import-source-drop-overlay"
					>
						<div className="text-primary inline-flex items-center gap-2 text-sm font-medium">
							<Upload size={14} aria-hidden="true" />
							Drop your file to import
						</div>
					</div>
				) : null}
			</div>
		</Dialog>
	);
}

/**
 * Dropzone tile shown when the "Upload a file" method is selected.
 *
 * Visually distinct from a "Choose file" button — a tall dashed-border
 * card with the file icon, the call-to-action, and (when a file is
 * already chosen) a filename + size + remove control.
 */
function UploadDropzone({
	fileName,
	fileSize,
	dragging,
	onPick,
	onClear,
}: {
	fileName: string | null;
	fileSize: number | null;
	dragging: boolean;
	onPick: () => void;
	onClear: () => void;
}) {
	const hasFile = fileName !== null;

	return (
		<button
			type="button"
			onClick={onPick}
			data-testid="import-source-dropzone"
			className={cn(
				'flex w-full flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-6 py-8 text-center transition-colors',
				dragging
					? 'border-primary/60 bg-primary/5'
					: hasFile
						? 'border-primary/40 bg-primary/5'
						: 'border-border/60 bg-muted/20 hover:border-border hover:bg-muted/30',
			)}
		>
			{hasFile ? (
				<div className="flex flex-col items-center gap-2">
					<span className="bg-primary/15 text-primary inline-flex h-9 w-9 items-center justify-center rounded-md">
						<FileText size={16} aria-hidden="true" />
					</span>
					<span
						className="text-foreground max-w-full truncate text-sm font-medium"
						data-testid="import-source-file-name"
					>
						{fileName}
					</span>
					{fileSize !== null ? (
						<span className="text-muted-foreground text-[11px]">
							{formatBytes(fileSize)}
						</span>
					) : null}
					<span
						role="button"
						tabIndex={0}
						onClick={(e) => {
							e.stopPropagation();
							onClear();
						}}
						onKeyDown={(e) => {
							if (e.key === 'Enter' || e.key === ' ') {
								e.preventDefault();
								e.stopPropagation();
								onClear();
							}
						}}
						className="text-muted-foreground hover:text-foreground mt-1 inline-flex cursor-pointer items-center gap-1 text-[11px] underline-offset-2 hover:underline"
						data-testid="import-source-file-clear"
					>
						<Trash2 size={11} aria-hidden="true" />
						Remove
					</span>
				</div>
			) : (
				<>
					<span className="bg-muted text-muted-foreground inline-flex h-9 w-9 items-center justify-center rounded-md">
						<Upload size={16} aria-hidden="true" />
					</span>
					<span className="text-foreground text-sm font-medium">
						Drop a file or <span className="text-primary">browse</span>
					</span>
					<span className="text-muted-foreground text-[11px]">
						.json, .yaml, .yml — up to a few MB
					</span>
				</>
			)}
		</button>
	);
}

function formatBytes(bytes: number): string {
	if (bytes < 1024) return `${bytes} B`;
	if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
	return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
