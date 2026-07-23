"""OpenAPI operation parser — extracts operation metadata from spec documents."""

from typing import Any

import structlog

from jentic_one.registry.ingest.observability import spec_path_count, tracer

logger = structlog.get_logger()

HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


class OpenAPIOperationParser:
    """Parses an OpenAPI spec and extracts operation definitions."""

    def extract_operations(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract all operations from an OpenAPI specification."""
        paths: dict[str, Any] = spec.get("paths", {})
        if not paths:
            logger.debug("no_paths_in_spec")
            return []

        with tracer.start_as_current_span("ingest.parse_openapi"):
            spec_path_count.record(len(paths))
            logger.debug("paths_found", count=len(paths))
            operations: list[dict[str, Any]] = []

            for path, path_item in paths.items():
                if not isinstance(path_item, dict):
                    continue

                path_servers: list[dict[str, Any]] = path_item.get("servers", [])
                path_parameters: list[dict[str, Any]] = [
                    p for p in path_item.get("parameters", []) if isinstance(p, dict)
                ]

                for method, operation in path_item.items():
                    if method not in HTTP_METHODS:
                        continue
                    if not isinstance(operation, dict):
                        continue

                    op = self._process_operation(
                        path, method, operation, path_servers, path_parameters
                    )
                    operations.append(op)

        logger.debug("operations_extracted", count=len(operations))
        return operations

    def _process_operation(
        self,
        path: str,
        method: str,
        operation: dict[str, Any],
        path_servers: list[dict[str, Any]],
        path_parameters: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Process a single operation into the canonical output shape.

        Retains ``parameters`` (query/header/path) and ``requestBody`` so the
        stored ``raw_operation`` records what inputs an operation accepts —
        without these, imported operations expose only path params and every
        write / header-bearing call is uncallable (issue #768).

        Path-item-level ``parameters`` apply to every operation on the path;
        they are merged in here with operation-level entries taking precedence
        on the same ``(name, in)`` key, mirroring the OpenAPI merge semantics
        already used by the catalogue preview projector.
        """
        operation_servers: list[dict[str, Any]] = operation.get("servers", [])
        servers = operation_servers if operation_servers else path_servers

        result: dict[str, Any] = {
            "operation_id": operation.get("operationId"),
            "path": path,
            "method": method.upper(),
            "summary": operation.get("summary"),
            "description": operation.get("description"),
            "tags": operation.get("tags", []),
            "servers": servers,
        }

        merged_parameters = self._merge_parameters(path_parameters, operation.get("parameters", []))
        if merged_parameters:
            result["parameters"] = merged_parameters

        if "requestBody" in operation:
            result["requestBody"] = operation["requestBody"]

        return result

    @staticmethod
    def _merge_parameters(
        path_parameters: list[dict[str, Any]],
        operation_parameters: Any,
    ) -> list[dict[str, Any]]:
        """Merge path-item and operation parameters.

        Operation-level parameters override path-level ones on the same
        ``(name, in)`` key. Non-dict entries (malformed specs) are skipped. A
        ``$ref`` parameter has neither ``name`` nor ``in`` in scope here, so it
        cannot participate in the override key — it is retained verbatim.
        """
        op_params = [p for p in operation_parameters if isinstance(p, dict)]
        op_keys = {(p.get("name"), p.get("in")) for p in op_params if "name" in p and "in" in p}
        merged: list[dict[str, Any]] = list(op_params)
        for param in path_parameters:
            key = (param.get("name"), param.get("in"))
            if "name" in param and "in" in param and key in op_keys:
                continue
            merged.append(param)
        return merged
