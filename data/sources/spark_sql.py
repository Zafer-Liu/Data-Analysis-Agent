"""Spark Thrift Server adapter with Spark SQL and LTZ/NTZ semantics."""
from __future__ import annotations

from data.cluster_connections import ClusterError
from data.sources.hive import HiveDataSource


class SparkSQLDataSource(HiveDataSource):
    kind, query_dialect = "spark_sql", "spark"
    protocol = "spark-thrift-hs2-v6"

    def _session_configuration(self):
        # Fixed backend-owned settings, never caller-supplied SQL SET or options.
        # Target acceptance must verify the server honors these session settings.
        return {"use:database": self.config["schema"], "spark.sql.session.timeZone": "UTC",
                "spark.sql.ansi.enabled": "true"}

    def get_capabilities(self):
        return {**super().get_capabilities(), "protocol": self.protocol,
                "spark_connect": False, "session_timezone": "UTC", "ansi_requested": True,
                "timestamp_semantics": {"timestamp": "LTZ, fixed UTC session", "timestamp_ltz": "UTC instant",
                                        "timestamp_ntz": "timezone-free; requires explicit type metadata"},
                "session_setting_acceptance": "requires target-server verification",
                "resource_pool": "administrator configured; no arbitrary confOverlay"}

    def _columns(self, columns):
        result = []
        for column in columns:
            name = str(column["type"]).lower().strip()
            if name == "timestamp":
                name = "timestamp_ltz"
            elif name == "timestamp with local time zone":
                name = "timestamp_ltz"
            elif name.startswith("timestamp") and name not in {"timestamp_ltz", "timestamp_ntz"}:
                raise ClusterError("unsupported_result_type", "Spark timestamp metadata does not establish LTZ/NTZ semantics")
            result.append({**column, "type": name})
        return result
