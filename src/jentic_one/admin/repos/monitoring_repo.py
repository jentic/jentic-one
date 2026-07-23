"""Repository for monitoring aggregation queries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Text, case, cast, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import ColumnElement, SQLColumnExpression

from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.shared.models import ExecutionStatus


def _is_postgres(session: AsyncSession) -> bool:
    """Whether the session's backend is PostgreSQL.

    The monitoring aggregations run on both Postgres (prod) and SQLite (local
    dev / `config/local-sqlite.yaml`), which diverge on a few functions. Defaults
    to Postgres when the bind is unknown (the prod backend).
    """
    return session.bind.dialect.name == "postgresql" if session.bind else True


def _epoch_seconds(session: AsyncSession) -> SQLColumnExpression[Any]:
    """Epoch seconds of ``started_at`` as a numeric expression, per dialect.

    Postgres has ``extract('epoch', ts)``; SQLite has no ``extract`` but
    ``strftime('%s', ts)`` yields the unix seconds (as text, cast to a number).
    """
    if _is_postgres(session):
        return func.extract("epoch", ExecutionRecord.started_at)
    return cast(func.strftime("%s", ExecutionRecord.started_at), Text)


def _slash_join(*parts: Any) -> SQLColumnExpression[Any]:
    """Join expressions with a literal ``/`` using the SQL ``||`` concat operator.

    Portable across Postgres and SQLite, unlike ``concat()`` (which SQLite lacks).
    ``||`` returns NULL if any operand is NULL, so callers coalesce beforehand
    exactly as the previous ``concat`` form required.
    """
    expr: SQLColumnExpression[Any] = parts[0]
    for part in parts[1:]:
        expr = expr.op("||")(part)
    return expr


@dataclass(slots=True, frozen=True)
class UsageQueryFilters:
    toolkit_id: str | None = None
    api_vendor: str | None = None
    api_name: str | None = None
    actor_id: str | None = None
    status: str | None = None

    def to_clauses(self) -> list[ColumnElement[bool]]:
        clauses: list[ColumnElement[bool]] = []
        if self.toolkit_id is not None:
            clauses.append(ExecutionRecord.toolkit_id == self.toolkit_id)
        if self.api_vendor is not None:
            clauses.append(ExecutionRecord.api_vendor == self.api_vendor)
        if self.api_name is not None:
            clauses.append(ExecutionRecord.api_name == self.api_name)
        if self.actor_id is not None:
            clauses.append(ExecutionRecord.actor_id == self.actor_id)
        if self.status is not None:
            clauses.append(ExecutionRecord.status == self.status)
        return clauses


@dataclass(slots=True, frozen=True)
class DailyBucketRow:
    date: str
    total: int
    success: int
    failed: int


@dataclass(slots=True, frozen=True)
class TopOperationRow:
    api_vendor: str
    api_name: str
    operation_id: str
    total: int
    failed: int


@dataclass(slots=True, frozen=True)
class OverallStatsRow:
    total: int
    success: int
    failed: int
    avg_ms: float
    p50_ms: float | None
    p95_ms: float | None


@dataclass(slots=True, frozen=True)
class TimeBucketRow:
    ts: int
    total: int
    success: int
    failed: int
    avg_ms: float


@dataclass(slots=True, frozen=True)
class GroupedTopRow:
    key: str
    label: str
    total: int
    success: int
    failed: int
    avg_ms: float


class MonitoringRepository:
    """Data access layer for execution monitoring aggregations — read-only."""

    @staticmethod
    async def daily_buckets(session: AsyncSession, cutoff: datetime) -> list[DailyBucketRow]:
        if _is_postgres(session):
            day_col = cast(func.date_trunc("day", ExecutionRecord.started_at), Text).label("day")
        else:
            # SQLite has no date_trunc; strftime yields a portable YYYY-MM-DD string.
            day_col = func.strftime("%Y-%m-%d", ExecutionRecord.started_at).label("day")
        stmt = (
            select(
                day_col,
                func.count().label("total"),
                func.count()
                .filter(ExecutionRecord.status == ExecutionStatus.COMPLETED)
                .label("success"),
                func.count()
                .filter(ExecutionRecord.status == ExecutionStatus.FAILED)
                .label("failed"),
            )
            .where(ExecutionRecord.started_at >= cutoff)
            .group_by(day_col)
            .order_by(day_col)
        )
        result = await session.execute(stmt)
        return [
            DailyBucketRow(
                date=str(row.day)[:10],
                total=row.total,
                success=row.success,
                failed=row.failed,
            )
            for row in result.all()
        ]

    @staticmethod
    async def top_operations(
        session: AsyncSession, cutoff: datetime, *, limit: int = 10
    ) -> list[TopOperationRow]:
        stmt = (
            select(
                ExecutionRecord.api_vendor,
                ExecutionRecord.api_name,
                ExecutionRecord.operation_id,
                func.count().label("total"),
                func.sum(
                    case(
                        (ExecutionRecord.status == ExecutionStatus.FAILED, 1),
                        else_=0,
                    )
                ).label("failed"),
            )
            .where(
                ExecutionRecord.started_at >= cutoff,
                ExecutionRecord.operation_id.is_not(None),
                ExecutionRecord.api_vendor.is_not(None),
                ExecutionRecord.api_name.is_not(None),
            )
            .group_by(
                ExecutionRecord.api_vendor,
                ExecutionRecord.api_name,
                ExecutionRecord.operation_id,
            )
            .order_by(func.count().desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        return [
            TopOperationRow(
                api_vendor=row.api_vendor,
                api_name=row.api_name,
                operation_id=row.operation_id,
                total=row.total,
                failed=row.failed,
            )
            for row in result.all()
        ]

    @staticmethod
    async def overall_stats(
        session: AsyncSession,
        cutoff: datetime,
        until: datetime,
        filters: UsageQueryFilters | None = None,
    ) -> OverallStatsRow:
        # SQLite has no percentile_cont / WITHIN GROUP ordered-set aggregate; only
        # Postgres computes p50/p95. On SQLite we omit them (the row type allows
        # None) so the counts/avg still come back rather than the whole statement
        # failing to parse.
        percentile_cols: list[SQLColumnExpression[Any]] = []
        if _is_postgres(session):
            percentile_cols = [
                func.percentile_cont(0.5).within_group(ExecutionRecord.duration_ms).label("p50_ms"),
                func.percentile_cont(0.95)
                .within_group(ExecutionRecord.duration_ms)
                .label("p95_ms"),
            ]
        stmt = select(
            func.count().label("total"),
            func.count()
            .filter(ExecutionRecord.status == ExecutionStatus.COMPLETED)
            .label("success"),
            func.count().filter(ExecutionRecord.status == ExecutionStatus.FAILED).label("failed"),
            func.coalesce(func.avg(ExecutionRecord.duration_ms), 0).label("avg_ms"),
            *percentile_cols,
        ).where(ExecutionRecord.started_at >= cutoff, ExecutionRecord.started_at < until)
        if filters:
            stmt = stmt.where(*filters.to_clauses())
        row = (await session.execute(stmt)).one()
        p50 = getattr(row, "p50_ms", None)
        p95 = getattr(row, "p95_ms", None)
        return OverallStatsRow(
            total=row.total,
            success=row.success,
            failed=row.failed,
            avg_ms=float(row.avg_ms),
            p50_ms=float(p50) if p50 is not None else None,
            p95_ms=float(p95) if p95 is not None else None,
        )

    @staticmethod
    async def time_buckets(
        session: AsyncSession,
        cutoff: datetime,
        until: datetime,
        bucket_seconds: int,
        filters: UsageQueryFilters | None = None,
    ) -> list[TimeBucketRow]:
        epoch_expr = _epoch_seconds(session)
        bucket_ts = (func.floor(epoch_expr / bucket_seconds) * literal(bucket_seconds)).label(
            "bucket_ts"
        )
        stmt = (
            select(
                cast(bucket_ts, Text).label("bucket_ts_text"),
                func.count().label("total"),
                func.count()
                .filter(ExecutionRecord.status == ExecutionStatus.COMPLETED)
                .label("success"),
                func.count()
                .filter(ExecutionRecord.status == ExecutionStatus.FAILED)
                .label("failed"),
                func.coalesce(func.avg(ExecutionRecord.duration_ms), 0).label("avg_ms"),
            )
            .where(ExecutionRecord.started_at >= cutoff, ExecutionRecord.started_at < until)
            .group_by(bucket_ts)
            .order_by(bucket_ts)
        )
        if filters:
            stmt = stmt.where(*filters.to_clauses())
        result = await session.execute(stmt)
        return [
            TimeBucketRow(
                ts=int(float(row.bucket_ts_text)),
                total=row.total,
                success=row.success,
                failed=row.failed,
                avg_ms=float(row.avg_ms),
            )
            for row in result.all()
        ]

    @staticmethod
    async def grouped_top(
        session: AsyncSession,
        cutoff: datetime,
        until: datetime,
        group_by: str,
        limit: int,
        filters: UsageQueryFilters | None = None,
    ) -> list[GroupedTopRow]:
        key_expr: SQLColumnExpression[Any]
        label_expr: SQLColumnExpression[Any]
        group_cols: list[SQLColumnExpression[Any]]
        if group_by == "api":
            key_expr = _slash_join(
                func.coalesce(ExecutionRecord.api_vendor, "unknown"),
                literal("/"),
                func.coalesce(ExecutionRecord.api_name, "unknown"),
            )
            label_expr = _slash_join(
                func.coalesce(ExecutionRecord.api_vendor, "unknown"),
                literal("/"),
                func.coalesce(ExecutionRecord.api_name, "unknown"),
            )
            group_cols = [ExecutionRecord.api_vendor, ExecutionRecord.api_name]
        elif group_by == "toolkit":
            key_expr = ExecutionRecord.toolkit_id
            label_expr = ExecutionRecord.toolkit_id
            group_cols = [ExecutionRecord.toolkit_id]
        else:
            key_expr = _slash_join(
                ExecutionRecord.actor_type, literal("/"), ExecutionRecord.actor_id
            )
            label_expr = _slash_join(
                ExecutionRecord.actor_type, literal("/"), ExecutionRecord.actor_id
            )
            group_cols = [ExecutionRecord.actor_type, ExecutionRecord.actor_id]

        stmt = (
            select(
                key_expr.label("key"),
                label_expr.label("label"),
                func.count().label("total"),
                func.count()
                .filter(ExecutionRecord.status == ExecutionStatus.COMPLETED)
                .label("success"),
                func.count()
                .filter(ExecutionRecord.status == ExecutionStatus.FAILED)
                .label("failed"),
                func.coalesce(func.avg(ExecutionRecord.duration_ms), 0).label("avg_ms"),
            )
            .where(ExecutionRecord.started_at >= cutoff, ExecutionRecord.started_at < until)
            .group_by(*group_cols)
            .order_by(func.count().desc())
            .limit(limit)
        )
        if filters:
            stmt = stmt.where(*filters.to_clauses())
        result = await session.execute(stmt)
        return [
            GroupedTopRow(
                key=row.key,
                label=row.label,
                total=row.total,
                success=row.success,
                failed=row.failed,
                avg_ms=float(row.avg_ms),
            )
            for row in result.all()
        ]

    @staticmethod
    async def grouped_trend(
        session: AsyncSession,
        cutoff: datetime,
        until: datetime,
        group_by: str,
        keys: list[str],
        num_points: int = 12,
        filters: UsageQueryFilters | None = None,
    ) -> dict[str, list[int]]:
        if not keys:
            return {}

        cutoff_epoch = cutoff.timestamp()
        until_epoch = until.timestamp()
        segment_seconds = (until_epoch - cutoff_epoch) / num_points

        key_expr: SQLColumnExpression[Any]
        if group_by == "api":
            key_expr = _slash_join(
                func.coalesce(ExecutionRecord.api_vendor, "unknown"),
                literal("/"),
                func.coalesce(ExecutionRecord.api_name, "unknown"),
            )
        elif group_by == "toolkit":
            key_expr = ExecutionRecord.toolkit_id
        else:
            key_expr = _slash_join(
                ExecutionRecord.actor_type, literal("/"), ExecutionRecord.actor_id
            )

        epoch_expr = _epoch_seconds(session)
        segment_idx = cast(
            func.floor((epoch_expr - literal(cutoff_epoch)) / literal(segment_seconds)),
            Text,
        ).label("seg")

        stmt = (
            select(
                key_expr.label("key"),
                segment_idx,
                func.count().label("cnt"),
            )
            .where(
                ExecutionRecord.started_at >= cutoff,
                ExecutionRecord.started_at < until,
                key_expr.in_(keys),
            )
            .group_by(key_expr, segment_idx)
        )
        if filters:
            stmt = stmt.where(*filters.to_clauses())
        result = await session.execute(stmt)

        trends: dict[str, list[int]] = {k: [0] * num_points for k in keys}
        for row in result.all():
            idx = int(float(row.seg))
            if 0 <= idx < num_points and row.key in trends:
                trends[row.key][idx] = row.cnt
        return trends
