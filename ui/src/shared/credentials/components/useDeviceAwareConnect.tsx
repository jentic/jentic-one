import { useCallback, useState, type ReactNode } from 'react';
import {
	useRunConnectFlow,
	type ConnectOutcome,
	type RunConnectOptions,
} from '@/shared/credentials/api';
import type { DeviceAuthorizationChallengeResponse } from '@/shared/credentials/api/types';
import { DeviceCodeConnectDialog } from '@/shared/credentials/components/DeviceCodeConnectDialog';

interface DeviceCodeState {
	challenge: DeviceAuthorizationChallengeResponse;
	credentialName: string;
	cancel: () => void;
}

/**
 * The OAuth connect every surface runs, with the device-code (RFC 8628) human
 * step wired in. `connect` behaves like `useRunConnectFlow`, but a
 * `device_authorization` challenge opens the code dialog instead of resolving
 * `unsupported_challenge`; the dialog's Cancel aborts the poll, which resolves
 * `cancelled`. Render `deviceDialog` once in the host.
 */
export function useDeviceAwareConnect(): {
	connect: (
		credentialId: string,
		credentialName: string,
		options?: Omit<RunConnectOptions, 'signal' | 'onDeviceAuthorizationChallenge'>,
	) => Promise<ConnectOutcome>;
	deviceDialog: ReactNode;
} {
	const runConnect = useRunConnectFlow();
	const [device, setDevice] = useState<DeviceCodeState | null>(null);

	const connect = useCallback(
		(
			credentialId: string,
			credentialName: string,
			options: Omit<RunConnectOptions, 'signal' | 'onDeviceAuthorizationChallenge'> = {},
		): Promise<ConnectOutcome> => {
			const controller = new AbortController();
			return runConnect(credentialId, {
				...options,
				signal: controller.signal,
				onDeviceAuthorizationChallenge: (challenge) => {
					setDevice({ challenge, credentialName, cancel: () => controller.abort() });
					return () => setDevice(null);
				},
			});
		},
		[runConnect],
	);

	const deviceDialog = (
		<DeviceCodeConnectDialog
			open={device != null}
			challenge={device?.challenge ?? null}
			credentialName={device?.credentialName ?? ''}
			onCancel={(): void => {
				device?.cancel();
				setDevice(null);
			}}
		/>
	);

	return { connect, deviceDialog };
}
