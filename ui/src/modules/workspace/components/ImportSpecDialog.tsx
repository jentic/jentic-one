/**
 * ImportSpecDialog — register a new API by importing an OpenAPI spec.
 *
 * Ported from jentic-mini's `ImportSourceDialog`, narrowed to **APIs only**
 * (no Arazzo/workflow kind — that's another module) and rewired to jentic-one's
 * **async** import contract: `POST /apis` returns 202 + a job id, then the hook
 * polls `/jobs/{id}` to a terminal state. On `succeeded` we toast + close; on
 * `failed` we keep the dialog open and surface the job's `error` inline (e.g.
 * the backend embeddings-extra gap verified against the live backend).
 *
 * Three input methods — URL / Paste / Upload — map onto the wire source shapes:
 *   url   → { type: 'url', url }
 *   paste → { type: 'inline', content, filename }
 *   file  → { type: 'inline', content, filename }
 *
 * State lifecycle (project convention): reset the draft ONLY on a successful
 * commit; dismissals (Esc, X) preserve it so a misclick doesn't lose a pasted
 * spec. Backdrop dismissal is disabled for the same reason.
 */
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import {
	AlertTriangle,
	ClipboardPaste,
	FileText,
	Globe,
	Loader2,
	Trash2,
	Upload,
} from 'lucide-react';
import { Button, Dialog, Input, Textarea } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import { OptionCardSelector } from '@/modules/workspace/components/OptionCardSelector';
import { useImportSpec } from '@/modules/workspace/api';
import type { ImportSource } from '@/modules/workspace/api';

type InputMode = 'url' | 'paste' | 'file';

export interface ImportSpecDialogProps {
	open: boolean;
	onClose: () => void;
}

const ACCEPTED_EXTENSIONS = '.json,.yaml,.yml,application/json,application/yaml,text/yaml';
const URL_PLACEHOLDER = 'https://example.com/openapi.json';
const PASTE_PLACEHOLDER =
	'{\n  "openapi": "3.1.0",\n  "info": { "title": "My API", … },\n  "paths": { … }\n}';

export function ImportSpecDialog({ open, onClose }: ImportSpecDialogProps) {
	const fieldId = useId();
	const { importSpec, isImporting } = useImportSpec();

	const [mode, setMode] = useState<InputMode>('url');
	const [urlValue, setUrlValue] = useState('');
	const [pasteValue, setPasteValue] = useState('');
	const [fileName, setFileName] = useState<string | null>(null);
	const [fileSize, setFileSize] = useState<number | null>(null);
	const [fileContent, setFileContent] = useState<string | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [dragging, setDragging] = useState(false);

	const fileInputRef = useRef<HTMLInputElement>(null);

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

	// Clear transient flags (error/dragging) on each re-open — they're state
	// from the last attempt, not user input, and would mislead next time.
	useEffect(() => {
		if (!open) return;
		setError(null);
		setDragging(false);
	}, [open]);

	const resetDraft = useCallback(() => {
		setMode('url');
		setUrlValue('');
		setPasteValue('');
		setFileName(null);
		setFileSize(null);
		setFileContent(null);
		setError(null);
		setDragging(false);
		if (fileInputRef.current) fileInputRef.current.value = '';
	}, []);

	const canSubmit = (() => {
		if (isImporting) return false;
		if (mode === 'url') return urlValue.trim().length > 0;
		if (mode === 'paste') return pasteValue.trim().length > 0;
		return fileContent !== null && fileContent.length > 0;
	})();

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
		const source: ImportSource =
			mode === 'url'
				? { type: 'url', url: urlValue.trim() }
				: mode === 'paste'
					? { type: 'inline', content: pasteValue, filename: 'pasted-spec.json' }
					: {
							type: 'inline',
							content: fileContent ?? '',
							filename: fileName ?? 'spec.json',
						};

		try {
			const job = await importSpec([source]);
			if (job.status === 'succeeded') {
				resetDraft();
				onClose();
				return;
			}
			// Failed / cancelled / timed-out: keep the dialog open with the reason.
			setError(
				job.error ||
					(job.status === 'failed'
						? 'Import failed. Check that the document is valid OpenAPI.'
						: `Import did not complete (status: ${job.status}).`),
			);
		} catch (err) {
			setError(
				err instanceof Error
					? err.message
					: 'Network error while importing. Please try again.',
			);
		}
	}

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
					loading={isImporting}
					fullWidth
					data-testid="import-spec-submit"
				>
					{isImporting ? 'Importing…' : 'Import API'}
				</Button>
			}
		>
			{/* Drop target is progressive enhancement on top of keyboard-accessible
			    controls — the <div> itself isn't interactive otherwise. */}
			<div
				className={cn(
					'relative flex flex-col gap-6 transition-colors',
					dragging && 'ring-primary/40 rounded-lg ring-2',
				)}
				data-testid="import-spec-dialog"
				onDragOver={handleDragOver}
				onDragLeave={handleDragLeave}
				onDrop={handleDrop}
			>
				<section className="space-y-3">
					<div>
						<h3 id={methodHeadingId} className="text-foreground text-sm font-medium">
							Choose import method
						</h3>
						<p className="text-muted-foreground mt-0.5 text-xs">
							OpenAPI 3.x as JSON or YAML — the server auto-detects the format.
						</p>
					</div>
					<OptionCardSelector
						options={methodOptions}
						value={mode}
						onChange={(v) => setMode(v)}
						columns={3}
						variant="compact"
						ariaLabelledBy={methodHeadingId}
						data-testid="import-spec-methods"
					/>
				</section>

				{mode === 'url' ? (
					<Input
						id={`${fieldId}-url`}
						type="url"
						autoFocus
						value={urlValue}
						onChange={(e) => setUrlValue(e.target.value)}
						placeholder={URL_PLACEHOLDER}
						data-testid="import-spec-url"
						className="py-3 font-mono"
					/>
				) : mode === 'paste' ? (
					<Textarea
						id={`${fieldId}-paste`}
						autoFocus
						value={pasteValue}
						onChange={(e) => setPasteValue(e.target.value)}
						placeholder={PASTE_PLACEHOLDER}
						rows={11}
						data-testid="import-spec-paste"
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

				<input
					ref={fileInputRef}
					type="file"
					accept={ACCEPTED_EXTENSIONS}
					className="hidden"
					onChange={handleFileChange}
					data-testid="import-spec-file-input"
				/>

				{error ? (
					<div
						role="alert"
						className="border-danger/30 bg-danger/5 text-danger flex items-start gap-2 rounded-md border-l-2 px-3 py-2 text-xs"
						data-testid="import-spec-error"
					>
						<AlertTriangle size={12} aria-hidden="true" className="mt-0.5 shrink-0" />
						<span className="leading-snug">{error}</span>
					</div>
				) : null}

				{isImporting ? (
					<div
						className="text-muted-foreground inline-flex items-center gap-2 text-xs"
						data-testid="import-spec-progress"
					>
						<Loader2 size={13} className="animate-spin" aria-hidden="true" />
						Importing — resolving and ingesting the spec. This can take a moment.
					</div>
				) : null}

				{/* Drop-target overlay — only while a file is dragged over the dialog. */}
				{dragging ? (
					<div
						className="bg-primary/5 border-primary/40 pointer-events-none absolute inset-0 -m-2 flex items-center justify-center rounded-lg border-2 border-dashed"
						aria-hidden="true"
						data-testid="import-spec-drop-overlay"
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
			data-testid="import-spec-dropzone"
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
						data-testid="import-spec-file-name"
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
						data-testid="import-spec-file-clear"
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
