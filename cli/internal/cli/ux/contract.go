package ux

// This file is the Go encoding of the machine contract (13_agent_machine_contract
// §3a/§4): the closed error_code enum and the exit-code taxonomy. It is the single
// source of truth for the code->exit mapping that CodedError.ExitCode() uses, so
// the emitted error_code and the process exit code can never drift apart.

// Exit-code taxonomy (13 §4). core.Run maps any ExitCoder to these; nothing calls
// os.Exit directly (arch rule).
const (
	ExitOK             = 0 // success (for execute: broker returned an upstream response, even a 4xx/5xx)
	ExitError          = 1 // generic/local failure: transport, missing arg, fencing, CLI bug
	ExitDenied         = 2 // Jentic said no: broker policy denial or resolve failure — change the ask, don't retry
	ExitTimeoutPending = 3 // --wait expired while still pending: retry later is meaningful
	ExitPartial        = 4 // partial approval of a composite request: inspect per-item status
)

// error_code enum (13 §3a). Closed set: agents treat an unknown code as
// INTERNAL_ERROR-like ("stop and report"). New codes are additive; add them here
// AND to errorCodeExit.
const (
	CodeMissingArgument    = "MISSING_ARGUMENT"
	CodeConfirmBlocked     = "INTERACTIVE_CONFIRM_BLOCKED"
	CodeFenced             = "FENCED_COMMAND"
	CodeNotAuthenticated   = "NOT_AUTHENTICATED"
	CodePendingApproval    = "PENDING_APPROVAL"
	CodeResolveFailed      = "RESOLVE_FAILED"
	CodeBrokerDenied       = "BROKER_DENIED"
	CodeTimeoutPending     = "TIMEOUT_PENDING"
	CodePartialApproval    = "PARTIAL_APPROVAL"
	CodeConfinementMissing = "CONFINEMENT_UNAVAILABLE"
	CodeTransportError     = "TRANSPORT_ERROR"
	CodeInternalError      = "INTERNAL_ERROR"
)

// errorCodeExit maps each closed error_code to its exit code (13 §3a "Typical
// exit" column). PENDING_APPROVAL is exit 1 here — the exit-3 variant is only for
// the --wait timeout, which surfaces as TIMEOUT_PENDING, a distinct code.
var errorCodeExit = map[string]int{
	CodeMissingArgument:    ExitError,
	CodeConfirmBlocked:     ExitError,
	CodeFenced:             ExitError,
	CodeNotAuthenticated:   ExitError,
	CodePendingApproval:    ExitError,
	CodeResolveFailed:      ExitDenied,
	CodeBrokerDenied:       ExitDenied,
	CodeTimeoutPending:     ExitTimeoutPending,
	CodePartialApproval:    ExitPartial,
	CodeConfinementMissing: ExitError,
	CodeTransportError:     ExitError,
	CodeInternalError:      ExitError,
}

// exitCodeFor returns the exit code for a closed error_code. An unknown/empty code
// maps to ExitError (1) — fail toward the generic-failure bucket, matching the
// "treat unknown as INTERNAL_ERROR" agent rule (13 §6).
func exitCodeFor(code string) int {
	if c, ok := errorCodeExit[code]; ok {
		return c
	}
	return ExitError
}
