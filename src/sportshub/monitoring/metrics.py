"""In-memory metrics counters (MVP — future: Prometheus integration)."""

from datetime import datetime


class Metrics:
    """Simple in-memory counters for observability."""

    def __init__(self) -> None:
        self.ingestion_runs_total: int = 0
        self.ingestion_errors_total: int = 0
        self.api_requests_total: int = 0
        self.cache_hits: int = 0
        self.cache_misses: int = 0
        self.resolution_runs_total: int = 0
        self.events_created_total: int = 0
        self.events_merged_total: int = 0
        self._source_last_success: dict[str, datetime] = {}
        self._source_records_total: dict[str, int] = {}

    def record_ingestion(self, source_id: str, success: bool, records: int = 0) -> None:
        self.ingestion_runs_total += 1
        if success:
            self._source_last_success[source_id] = datetime.utcnow()
            self._source_records_total[source_id] = (
                self._source_records_total.get(source_id, 0) + records
            )
        else:
            self.ingestion_errors_total += 1

    def record_cache_access(self, hit: bool) -> None:
        if hit:
            self.cache_hits += 1
        else:
            self.cache_misses += 1

    def get_source_last_success(self, source_id: str) -> datetime | None:
        return self._source_last_success.get(source_id)

    def to_dict(self) -> dict:
        return {
            "ingestion_runs_total": self.ingestion_runs_total,
            "ingestion_errors_total": self.ingestion_errors_total,
            "api_requests_total": self.api_requests_total,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "cache_hit_ratio": (
                self.cache_hits / (self.cache_hits + self.cache_misses)
                if (self.cache_hits + self.cache_misses) > 0
                else 0.0
            ),
        }


# Global singleton
metrics = Metrics()
