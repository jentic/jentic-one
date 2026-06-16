import { ApiPicker } from '@/components/credentials/form/ApiPicker';
import type { ApiOut } from '@/api/types';

/**
 * Step 1 of `<AddCredentialDialog>` — pick which API the credential
 * is for. A thin wrapper around the lifted `<ApiPicker>` so the step
 * file mirrors the others in shape (own file, named export, single
 * `props` interface) and so future per-step concerns (toolkit-aware
 * filters, "recently used APIs" surface, etc.) have a clear home.
 */
export interface SearchApiStepProps {
	onSelect: (api: ApiOut) => void;
}

export function SearchApiStep({ onSelect }: SearchApiStepProps) {
	return (
		<div className="space-y-4">
			<p className="text-muted-foreground text-sm">
				Pick the upstream API this credential is for. Catalog APIs are imported into your
				workspace as part of saving the credential — there's no separate import step.
			</p>
			<ApiPicker onSelect={onSelect} />
		</div>
	);
}
