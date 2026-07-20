import { http, HttpResponse } from 'msw';
import { bundleForLocal, listSessionsLocal } from '@/modules/llm-proxy/lib/mockData';

/**
 * LLM Proxy · Sessions MSW handlers.
 *
 * Serves the single consolidated mock document (`sessions-mock.json`, the
 * source of truth while there is no backend — see the plan doc) over the
 * endpoints the future backend will expose:
 *   - GET /proxy/sessions        → list of session summaries
 *   - GET /proxy/sessions/:id     → the full bundle (agents + calls + chat + …)
 *
 * The shaping lives in `lib/mockData` so the repository tier can reuse it as a
 * fallback when MSW is off and no backend serves `/proxy/*`. When the real
 * backend lands, delete these handlers, the JSON, and `lib/mockData`; the
 * repository tier already speaks these paths.
 */
export const llmProxyHandlers = [
	http.get('/proxy/sessions', () => HttpResponse.json(listSessionsLocal())),
	http.get('/proxy/sessions/:id', ({ params }) => {
		const bundle = bundleForLocal(String(params.id));
		return bundle ? HttpResponse.json(bundle) : new HttpResponse(null, { status: 404 });
	}),
];
