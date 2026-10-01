/**
 * Where a pasted `jentic register` points: this instance's control-plane URL
 * and, on a remote install, its broker. Every surface that shows the command
 * reads it here, so the landing and the MCP sheet never disagree.
 */
import { useInstanceIdentity } from '@/modules/agents/api';
import type { RegisterCommandOptions } from '@/modules/agents/lib/registerCommand';

export interface RegisterTarget extends Pick<RegisterCommandOptions, 'url' | 'brokerUrl'> {
	/** The instance's declared locality (`local` / `remote`), once `GET /instance` answers. */
	backend: string | undefined;
}

/**
 * The operator is looking at a working address of this instance, so the
 * browser origin is the honest fallback when no canonical base URL is
 * configured (or `GET /instance` failed).
 *
 * On a remote install the broker lives on its own host and is never derived
 * from the control-plane URL — without `--broker-url` the CLI's environment has
 * no broker and `jentic execute` fail-closes. `brokerUrl` is then the reported
 * URL, or `null` (the command's placeholder) when the instance can't honestly
 * advertise one; an empty string counts as not advertised. On a local install
 * it is absent and the flag is omitted.
 */
export function useRegisterTarget(): RegisterTarget {
	const identity = useInstanceIdentity();
	const url = identity.data?.baseUrl || window.location.origin;
	const backend = identity.data?.backend;
	if (backend === 'remote') {
		return { url, brokerUrl: identity.data?.brokerUrl || null, backend };
	}
	return { url, backend };
}
