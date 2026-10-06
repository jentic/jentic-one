import { AlertTriangle } from 'lucide-react';
import { Button } from '@/shared/ui';
import {
	credentialDistinguisher,
	type CredentialNameClash,
} from '@/shared/credentials/lib/credentialIdentity';

interface CredentialNameClashNoteProps extends CredentialNameClash {
	/** Wired to the Name input's `aria-describedby`. */
	id: string;
	onUseSuggestion: (name: string) => void;
}

/**
 * Warns, under a credential's Name field, that another credential for the API
 * already holds the name, and offers a free one. Never blocks the save: the
 * backend accepts duplicates, but a caller picking a credential by name
 * (`Jentic-Credential-Name`) needs the names to differ.
 */
export function CredentialNameClashNote({
	id,
	clash,
	suggestion,
	onUseSuggestion,
}: CredentialNameClashNoteProps) {
	return (
		<div
			id={id}
			role="status"
			data-testid="credential-name-clash"
			className="border-warning/40 bg-warning/5 rounded-lg border p-3 text-xs"
		>
			<div className="flex items-start gap-2">
				<AlertTriangle
					className="text-warning mt-px h-3.5 w-3.5 shrink-0"
					aria-hidden="true"
				/>
				<p className="text-foreground leading-snug">
					You already have a credential named{' '}
					<span className="font-medium">{clash.name}</span> for this API
					<span className="text-muted-foreground">
						{' '}
						({credentialDistinguisher(clash)})
					</span>
					. A different name tells them apart.
				</p>
			</div>
			<div className="mt-2 flex flex-wrap items-center gap-2 pl-5.5">
				<span className="text-muted-foreground">
					Suggested: <span className="text-foreground font-medium">{suggestion}</span>
				</span>
				<Button
					type="button"
					variant="secondary"
					size="sm"
					className="h-6 px-2 text-xs"
					onClick={(): void => onUseSuggestion(suggestion)}
				>
					Use this name
				</Button>
			</div>
		</div>
	);
}
