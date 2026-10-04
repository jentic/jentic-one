import { AlertTriangle, Copy, Check, RefreshCw } from 'lucide-react';
import { useState } from 'react';
import { ApiError } from '@/shared/api';
import { Button } from '@/shared/ui/Button';
import { cn } from '@/shared/lib/utils';

interface ErrorAlertProps {
	/** Either a raw message string or an Error / ApiError instance. */
	message: string | Error;
	className?: string;
	/**
	 * Optional retry handler. When provided, a "Try again" button is rendered so
	 * a transient failure isn't a dead end.
	 */
	onRetry?: () => void;
	/** Disable the retry button (e.g. while a refetch is already in flight). */
	retrying?: boolean;
}

type NoSchemeData = Record<string, unknown> & {
	api_id?: string;
	message?: string;
	instructions?: string;
	submit_to?: string;
	note?: string;
	examples?: Record<string, Record<string, unknown> & { _note?: string }>;
};

/** Build a single copyable markdown string from the no_security_scheme error data. */
function buildNoSchemeMarkdown(data: NoSchemeData): string {
	const overlayEndpoint = data.submit_to ?? `POST /apis/${data.api_id}/overlays`;
	const examples = data.examples ?? {};

	const lines: string[] = [];

	lines.push(`## No security scheme for \`${data.api_id}\``);
	lines.push('');
	lines.push(String(data.message ?? ''));
	lines.push('');
	lines.push('### Instructions');
	lines.push('');
	lines.push(String(data.instructions ?? ''));
	lines.push('');
	lines.push(`Submit the overlay to: \`${overlayEndpoint}\``);
	if (data.note) {
		lines.push('');
		lines.push(`> **Note:** ${data.note}`);
	}

	const exampleKeys = Object.keys(examples);
	if (exampleKeys.length > 0) {
		lines.push('');
		lines.push('### Overlay examples');
		lines.push('');
		lines.push(
			'Pick the pattern that matches how this API authenticates, fill in the real header/parameter names, and POST it to the endpoint above.',
		);

		for (const key of exampleKeys) {
			const ex = { ...examples[key] };
			// Strip internal _note from JSON output but include it as prose.
			const prose = ex._note;
			delete ex._note;

			const label = key.replaceAll('_', ' ');
			lines.push('');
			lines.push(`#### ${label}`);
			if (prose) {
				lines.push('');
				lines.push(prose);
			}
			lines.push('');
			lines.push('```json');
			lines.push(JSON.stringify(ex, null, 2));
			lines.push('```');
		}
	}

	return lines.join('\n');
}

/** Render a structured `no_security_scheme` API error as a single copyable block. */
function NoSchemeError({ data }: { data: NoSchemeData }) {
	const [copied, setCopied] = useState(false);
	const overlayEndpoint = data.submit_to ?? `POST /apis/${data.api_id}/overlays`;
	const examples = data.examples ?? {};
	const exampleKeys = Object.keys(examples);
	const markdown = buildNoSchemeMarkdown(data);

	const copy = () => {
		navigator.clipboard.writeText(markdown).then(() => {
			setCopied(true);
			setTimeout(() => setCopied(false), 2000);
		});
	};

	return (
		<div className="space-y-3 text-xs">
			<div className="flex items-start justify-between gap-2">
				<p className="text-sm leading-snug font-medium">{data.message}</p>
				<Button
					variant="tonal"
					size="xs"
					type="button"
					onClick={copy}
					title="Copy everything as markdown"
					className={cn('shrink-0', copied && 'text-success hover:text-success')}
				>
					{copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
					{copied ? 'Copied' : 'Copy as markdown'}
				</Button>
			</div>

			<div className="bg-background/60 space-y-1.5 rounded-md p-3">
				<p className="text-muted-foreground text-[10px] font-semibold tracking-wide uppercase">
					Instructions for your agent
				</p>
				<p className="leading-relaxed">{data.instructions}</p>
				<p className="text-muted-foreground">
					Submit to:{' '}
					<code className="bg-surface-field text-foreground rounded-sm px-1 py-0.5 font-mono">
						{overlayEndpoint}
					</code>
				</p>
				{data.note && (
					<p className="text-muted-foreground border-hairline mt-1.5 border-t pt-1.5 italic">
						{data.note}
					</p>
				)}
			</div>

			{exampleKeys.length > 0 && (
				<div className="space-y-2">
					<p className="text-muted-foreground text-[10px] font-semibold tracking-wide uppercase">
						Overlay examples — pick the right pattern, fill in header names, submit
					</p>
					{exampleKeys.map((key) => {
						const ex = { ...examples[key] };
						const prose = ex._note;
						delete ex._note;
						const label = key.replaceAll('_', ' ');
						return (
							<div key={key} className="bg-background/60 overflow-hidden rounded-md">
								<div className="bg-background/40 px-3 py-1.5">
									<span className="text-foreground font-mono text-[11px] font-semibold">
										{label}
									</span>
									{prose && (
										<p className="text-muted-foreground mt-0.5 leading-relaxed">
											{prose}
										</p>
									)}
								</div>
								<pre className="overflow-x-auto p-3 text-[11px] leading-relaxed">
									{JSON.stringify(ex, null, 2)}
								</pre>
							</div>
						);
					})}
				</div>
			)}
		</div>
	);
}

export function ErrorAlert({ message, className, onRetry, retrying }: ErrorAlertProps) {
	// The rich `no_security_scheme` payload lives on the generated `ApiError.body`.
	// Module repositories often wrap that in a domain error (e.g. Monitor's
	// `MonitorApiError`) and stash the original on `.cause`, so unwrap one level
	// to recover the structured body when present.
	const cause = (message as { cause?: unknown } | null)?.cause;
	const apiError =
		message instanceof ApiError ? message : cause instanceof ApiError ? cause : null;
	const apiData = (apiError ? apiError.body : null) as NoSchemeData | null;
	const errorCode = apiData?.error as string | undefined;
	const text = typeof message === 'string' ? message : message.message;

	return (
		<div
			role="alert"
			className={cn('bg-danger/10 text-danger rounded-lg px-4 py-3 text-sm', className)}
		>
			<div className="flex items-start gap-3">
				<AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
				<div className="min-w-0 flex-1">
					{errorCode === 'no_security_scheme' && apiData ? (
						<NoSchemeError data={apiData} />
					) : (
						<span>{text}</span>
					)}
					{onRetry && (
						<div className="mt-3">
							<Button
								variant="outline"
								size="sm"
								type="button"
								onClick={onRetry}
								disabled={retrying}
								className="inline-flex items-center gap-1.5"
							>
								<RefreshCw
									className={cn('h-3.5 w-3.5', retrying && 'animate-spin')}
									aria-hidden="true"
								/>
								{retrying ? 'Retrying…' : 'Try again'}
							</Button>
						</div>
					)}
				</div>
			</div>
		</div>
	);
}
