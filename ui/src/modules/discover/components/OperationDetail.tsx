/**
 * OperationDetail (discover) — thin adapter over the shared `OperationDetail`.
 *
 * The presentational Parameters/Auth tables now live in `@/shared/ui` so the
 * workspace module can render spec-derived operations with the same visual
 * language. This wrapper projects the generated catalog preview type
 * (`PreviewOperationResponse`) onto the shared neutral shape.
 */
import {
	OperationDetail as SharedOperationDetail,
	type OperationDetailData,
	type SecuritySchemeMap,
} from '@/shared/ui';
import type { PreviewOperationResponse } from '@/modules/discover/api';

interface OperationDetailProps {
	operation: PreviewOperationResponse;
	securitySchemes: SecuritySchemeMap;
}

export function OperationDetail({ operation, securitySchemes }: OperationDetailProps) {
	const data: OperationDetailData = {
		method: operation.method,
		path: operation.path,
		summary: operation.summary,
		description: operation.description,
		parameters: operation.parameters.map((p) => ({
			name: p.name,
			in: p.in,
			required: p.required,
			description: p.description,
		})),
		security: operation.security ?? [],
	};
	return <SharedOperationDetail operation={data} securitySchemes={securitySchemes} />;
}
