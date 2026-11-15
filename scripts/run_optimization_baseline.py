"""Execute fixed local tool contracts or compare operator-imported actual trials.

This command never calls a model, network service, provider SDK or model loader.
MCP, Workflow and member generation use explicit local adapters. Their outputs
are checked through real SQL, files, artifacts, reviews and cancellation scopes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from types import SimpleNamespace
from typing import Any, Callable

# Permit an absolute script invocation from a different working directory.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from data.cost_baseline import load_manifest, markdown_report, summarize, validate_trial
from data.optimization_trials import evaluate_optimization_gate, manifest_fingerprint, prompt_fingerprint


_WINDOW = "status='paid' AND order_date>='2026-01-01' AND order_date<'2026-03-01'"
_GROSS = f"SELECT SUM(amount) FROM orders WHERE {_WINDOW} AND amount IS NOT NULL"
_NET = f"""WITH r AS (
    SELECT order_id,SUM(amount) AS refunded FROM refunds WHERE status='confirmed' GROUP BY order_id
) SELECT SUM(o.amount-COALESCE(r.refunded,0)) FROM orders o
LEFT JOIN r ON o.id=r.order_id WHERE {_WINDOW} AND o.amount IS NOT NULL"""
# Qualify ambiguous status when joining refunds' aggregate.
_NET = _NET.replace("WHERE status='paid'", "WHERE o.status='paid'")
_RATE = f"""SELECT COUNT(DISTINCT o.id),COUNT(DISTINCT CASE WHEN r.id IS NOT NULL THEN o.id END)
FROM orders o LEFT JOIN refunds r ON r.order_id=o.id AND r.status='confirmed'
WHERE {_WINDOW.replace("status=", "o.status=")}"""


def _write_json(path: Path, value: Any) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def _hash(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class LocalTools:
    """Measured local operations; calls count attempts, including controlled faults."""

    def __init__(self, root: Path, snapshot: dict):
        self.root, self.snapshot = root, snapshot
        self.calls: list[str] = []
        self.evidence: list[str] = []
        self.retry_count = 0
        self.failure_stage = "none"
        self.delivery_count = 0
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript("""
            CREATE TABLE orders(id INTEGER,customer TEXT,order_date TEXT,amount REAL,status TEXT);
            CREATE TABLE refunds(id INTEGER,order_id INTEGER,amount REAL,status TEXT);
            CREATE VIRTUAL TABLE documents USING fts5(id UNINDEXED,body);
        """)
        self.conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?)", snapshot["orders"])
        self.conn.executemany("INSERT INTO refunds VALUES (?,?,?,?)", snapshot["refunds"])
        self.conn.executemany("INSERT INTO documents VALUES (?,?)", snapshot["documents"])

    def call(self, name: str, operation: Callable[[], Any]) -> Any:
        self.calls.append(name)
        try:
            result = operation()
        except Exception:
            self.evidence.append(_hash({"operation": name, "status": "failed", "attempt": len(self.calls)}))
            raise
        self.evidence.append(_hash({"operation": name, "result": result}))
        return result

    def query(self, sql: str) -> list[list]:
        return self.call("query_data", lambda: [list(row) for row in self.conn.execute(sql)])

    def persist(self, value: Any, name: str = "delivery.json") -> Any:
        def operation():
            target = self.root / name
            _write_json(target, value)
            return json.loads(target.read_text(encoding="utf-8"))
        return self.call("write_read_delivery", operation)

    def adapter_query(self, arguments: dict) -> dict:
        """A controlled MCP transport boundary with real local query execution."""
        if arguments != {"operation": "window_gross", "source_id": self.snapshot["source_id"]}:
            raise ValueError("controlled adapter rejected arguments")
        return {"source_id": self.snapshot["source_id"], "rows": self.query(_GROSS)}

    def execute(self, category: str) -> dict[str, bool]:
        expected = self.snapshot["expected"]
        if category == "single_table_query":
            value = self.query(_GROSS)[0][0]
            return {"source_window": len(self.query(f"SELECT id FROM orders WHERE {_WINDOW}")) == 4,
                    "gross_result": value == expected["window_gross"]}
        if category == "cross_table_analysis":
            value = self.query(_NET)[0][0]
            counts = self.query("SELECT order_id,COUNT(*) FROM refunds WHERE status='confirmed' GROUP BY order_id")
            return {"join_grain": [3, 2] in counts, "net_result": value == expected["window_net"]}
        if category == "knowledge_retrieval":
            rows = self.call("query_knowledge", lambda: self.conn.execute(
                "SELECT id FROM documents WHERE documents MATCH ?", ("refund",)).fetchall())
            empty = self.call("query_knowledge", lambda: self.conn.execute(
                "SELECT id FROM documents WHERE documents MATCH ?", ("astronomy",)).fetchall())
            return {"policy_reference": rows == [("refund-policy",)], "unrelated_empty": empty == []}
        if category == "long_report":
            from agent.tools.results import make_tool_result, read_tool_result_artifact
            value = self.query(_GROSS)[0][0]
            text = f"Summary\nGross revenue: {value}\nEvidence\n" + (
                "Paid orders only; order date January-February 2026; null amounts excluded.\n" * 60
            ) + "Recommendations\nValidate future data before interpreting changes.\n"
            runtime = SimpleNamespace(cache_dir=self.root / "cache", workspace_id="synthetic-workspace")
            envelope = self.call("make_tool_result", lambda: make_tool_result(
                "export_report", text, session_id="offline-session", runtime=runtime,
            ).to_dict())
            artifact = next(item for item in envelope["artifacts"] if item["type"] == "tool_result")
            chunks, offset = [], 0
            while True:
                page = self.call("read_tool_result", lambda: read_tool_result_artifact(
                    artifact["artifact_id"], allowed_artifacts=envelope["artifacts"],
                    session_id="offline-session", workspace_id="synthetic-workspace",
                    runtime=runtime, offset=offset, limit=1200,
                ))
                chunks.append(page["content"])
                if page["next_offset"] is None:
                    break
                offset = page["next_offset"]
            return {"report_sections": value == expected["window_gross"] and all(section in text for section in ("Summary", "Evidence", "Recommendations")),
                    "full_artifact": "".join(chunks) == text and len(str(envelope["data"])) < len(text)}
        if category == "chart":
            rows = self.query("SELECT substr(order_date,1,7),SUM(amount) FROM orders WHERE status='paid' GROUP BY 1 ORDER BY 1")
            spec = self.persist({"type": "bar", "x": "month", "y": "paid_revenue", "source_id": self.snapshot["source_id"], "rows": rows})
            return {"chart_spec": spec["type"] == "bar" and spec["source_id"] == "synthetic-shop-v1",
                    "data_basis": spec["rows"] == [["2026-01", 300], ["2026-02", 200], ["2026-03", 120]]}
        if category == "file_read":
            text = self.snapshot["documents"][0][1]
            path = self.root / "policy.txt"
            path.write_text(text, encoding="utf-8", newline="\n")
            def read():
                with path.open(encoding="utf-8") as stream:
                    return stream.read(64)
            content = self.call("workspace_read_file", read)
            digest = self.call("file_hash", lambda: hashlib.sha256(path.read_bytes()).hexdigest())
            return {"file_hash": digest == hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "bounded_read": content == text[:64] and len(content) == 64}
        if category == "mcp_query":
            output = self.call("mcp_local_adapter", lambda: self.adapter_query({
                "operation": "window_gross", "source_id": self.snapshot["source_id"],
            }))
            return {"adapter_identity": output["source_id"] == "synthetic-shop-v1",
                    "adapter_result": output["rows"] == [[expected["window_gross"]]]}
        if category == "workflow":
            gross = self.query(_GROSS)[0][0]
            transformed = self.call("workflow_transform_adapter", lambda: {"gross": gross, "unit": "currency_units"})
            self.persist({"status": "succeeded", "output": transformed, "delivery_count": 1}, "run.json")
            count = len(self.calls)
            restored = json.loads((self.root / "run.json").read_text(encoding="utf-8"))
            return {"node_evidence": restored["output"]["gross"] == expected["window_gross"] and len(self.evidence) == 3,
                    "reconnect_once": len(self.calls) == count and restored["delivery_count"] == 1}
        if category == "dynamic_team":
            from agent.teams.review import parse_quality_review, quality_review_blocks
            analyst = self.query(_NET)[0][0]
            denominator, numerator = self.query(_RATE)[0]
            member_refs = list(self.evidence)
            review = self.call("review_adapter", lambda: parse_quality_review({
                "decision": "pass" if analyst == expected["window_net"] and numerator / denominator * 100 == expected["refund_rate_percent"] else "rework",
                "issues": [] if analyst == expected["window_net"] else ["net mismatch"],
                "evidence": member_refs, "missing_evidence": [],
            }))
            blocked = quality_review_blocks({"decision": "rework", "issues": ["missing material"], "evidence": [], "missing_evidence": ["claim"]})
            return {"member_evidence": len(member_refs) == 2 and len(review["evidence"]) == 2,
                    "review_gate": not quality_review_blocks(review) and blocked}
        if category == "failure_retry":
            def fault():
                raise TimeoutError("controlled transport timeout")
            try:
                self.call("mcp_local_adapter", fault)
            except TimeoutError:
                self.retry_count += 1
            result = self.call("mcp_local_adapter", lambda: self.adapter_query({
                "operation": "window_gross", "source_id": self.snapshot["source_id"],
            }))
            return {"failure_recorded": self.retry_count == 1 and self.calls.count("mcp_local_adapter") == 2,
                    "retry_result": result["rows"] == [[expected["window_gross"]]]}
        if category == "data_quality":
            nulls = self.query("SELECT COUNT(*) FROM orders WHERE amount IS NULL")[0][0]
            total, distinct = self.query("SELECT COUNT(*),COUNT(DISTINCT id) FROM orders")[0]
            return {"null_rule": nulls == expected["null_amount_count"], "unique_grain": total == distinct == 6}
        if category == "structured_delivery":
            denominator, numerator = self.query(_RATE)[0]
            result = self.persist({"value": numerator / denominator * 100, "unit": "percent", "numerator": numerator,
                                   "denominator": denominator, "evidence": list(self.evidence), "definition": self.snapshot["definition"]})
            return {"structured_format": result["unit"] == "percent" and result["value"] == expected["refund_rate_percent"] and bool(result["evidence"]),
                    "denominator": result["denominator"] == expected["window_paid_orders"] and result["numerator"] == expected["window_refunded_orders"]}
        if category == "semantic_revision":
            old = self.persist(self.snapshot["definition"], "historic.json")
            current = dict(old, denominator="all orders including canceled")
            self.persist(current, "current.json")
            historic = self.call("read_historic_definition", lambda: json.loads((self.root / "historic.json").read_text(encoding="utf-8")))
            return {"immutable_revision": old["version"] == current["version"] and _hash(old) != _hash(current),
                    "historic_rule": historic == old and historic["denominator"] == "distinct paid orders"}
        if category == "cancellation_replay":
            from agent.execution import ExecutionCancelled, ExecutionContext
            from data.cost_event_store import CostEventStore
            context = ExecutionContext(request_id="offline-cancel")
            child = context.child()
            try:
                child.cancel("controlled cancellation")
                blocked = False
                try:
                    child.checkpoint()
                    self.query(_GROSS)
                except ExecutionCancelled:
                    blocked = True
                context.acknowledge_exit()
                retained = not context.exit_acknowledged
            finally:
                child.acknowledge_exit()
                context.acknowledge_exit()
            ledger = CostEventStore(self.root / "costs.sqlite3")
            receipt = dict(event_id="receipt", occurred_at="2026-11-14T00:00:00Z", scope_type="session",
                           session_id="offline-session", status="canceled", usage_source="unavailable", measurement_mode="offline_contract")
            self.call("record_canceled_receipt", lambda: ledger.record(**receipt))
            self.call("replay_canceled_receipt", lambda: ledger.record(**receipt))
            summary = ledger.summary(session_id="offline-session")
            return {"canceled_boundary": blocked and retained and context.exit_acknowledged and "query_data" not in self.calls,
                    "receipt_replay": summary["event_count"] == 1 and summary["input_tokens"] is None and summary["estimated_cost"] is None}
        raise ValueError("unsupported offline category")


def run_offline_baseline(
    manifest_path: Path | str, *, trials: int = 3, output_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Run every actual local operation; report only hashes and measured metrics."""
    manifest_path = Path(manifest_path)
    manifest = load_manifest(manifest_path)
    snapshot_path = (manifest_path.parent / manifest["snapshot_file"]).resolve()
    if not snapshot_path.is_relative_to(manifest_path.parent.resolve()):
        raise ValueError("snapshot must stay inside the fixture directory")
    data = snapshot_path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != manifest["snapshot_sha256"] or any(case["snapshot_id"] != "sha256:" + digest for case in manifest["cases"]):
        raise ValueError("fixed snapshot hash mismatch")
    if isinstance(trials, bool) or not isinstance(trials, int) or trials < max(case["min_trials"] for case in manifest["cases"]):
        raise ValueError("at least the manifest minimum trial count is required")
    snapshot = json.loads(data)
    records = []
    with tempfile.TemporaryDirectory(prefix="baa-cost-ops-") as temporary:
        for case in manifest["cases"]:
            for trial in range(1, trials + 1):
                trial_root = Path(temporary) / case["id"] / str(trial)
                trial_root.mkdir(parents=True)
                started = time.perf_counter_ns()
                tools = LocalTools(trial_root, snapshot)
                try:
                    checks = tools.execute(case["category"])
                    passed = all(checks.get(key) is True for key in case["expected_evidence"])
                    status = "succeeded" if passed else "failed"
                    failure_stage = "none" if passed else "output"
                except Exception:
                    checks, passed, status, failure_stage = {}, False, "failed", "tool"
                finally:
                    tools.conn.close()
                elapsed = (time.perf_counter_ns() - started) / 1_000_000
                record = {
                    "case_id": case["id"], "trial_id": f"offline-{trial:02d}", "pair_id": f"trial-{trial:02d}",
                    "variant": "offline", "policy_id": "baseline-contract-v1", "manifest_hash": manifest_fingerprint(manifest),
                    "snapshot_id": case["snapshot_id"], "prompt_hash": prompt_fingerprint(case["prompt"]),
                    "measurement_mode": "offline_contract", "usage_source": "unavailable", "usage_complete": False,
                    "status": status, "provider": "local-controlled-adapters", "model": "none", "price_version": "unavailable",
                    "currency": "", "cache_config": "disabled", "team_config": "local-adapter-v1",
                    "runtime_version": f"python-{sys.version_info.major}.{sys.version_info.minor}", "tool_contract_version": "offline-local-v1",
                    "input_tokens": None, "output_tokens": None, "cached_input_tokens": None, "cache_write_tokens": None,
                    "estimated_cost": None, "cost_source": "unavailable", "elapsed_ms": round(elapsed, 6),
                    "tool_call_count": len(tools.calls), "retry_count": tools.retry_count, "rework_count": 0,
                    "quality_pass": passed, "evidence_complete": passed and bool(tools.evidence), "delivery_pass": passed,
                    "delivery_count": int(passed), "model_call_ids": [], "evidence_refs": list(dict.fromkeys(tools.evidence)),
                    "assertions": checks, "failure_stage": failure_stage,
                }
                validate_trial(record, strict=True)
                records.append(record)
    summary = summarize(manifest, records)
    gate = evaluate_optimization_gate(manifest, records, records, policy_id="context-v1")
    result = {"manifest_hash": manifest_fingerprint(manifest), "trials": records, "summary": summary,
              "gate": gate, "limitations": [
                  "Local operations only; no model, provider usage, billed cost or LLM quality was measured.",
                  "MCP transport, Workflow orchestration and team generation use controlled local adapters.",
                  "Chart scenarios validate specifications and data basis; no browser renderer is exercised.",
                  "Definition snapshots test local immutability; production ontology integration has separate regressions.",
              ]}
    if output_dir is not None:
        target = Path(output_dir)
        target.mkdir(parents=True, exist_ok=True)
        with (target / "trials.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
            for record in records:
                stream.write(json.dumps(record, ensure_ascii=True, sort_keys=True, allow_nan=False) + "\n")
        _write_json(target / "summary.json", {key: value for key, value in result.items() if key != "trials"})
        with (target / "report.md").open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(markdown_report(summary))
            stream.write("\n" + "\n".join("- " + item for item in result["limitations"]) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(_ROOT / "tests/fixtures/optimization_cost/manifest.json"))
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--import-before")
    parser.add_argument("--import-after")
    parser.add_argument("--policy-id", default="context-v1")
    args = parser.parse_args()
    if bool(args.import_before) != bool(args.import_after):
        parser.error("--import-before and --import-after must be supplied together")
    if args.import_before:
        gate = evaluate_optimization_gate(load_manifest(args.manifest), args.import_before, args.import_after, policy_id=args.policy_id)
        target = Path(args.output_dir)
        target.mkdir(parents=True, exist_ok=True)
        _write_json(target / "gate.json", gate)
        print(json.dumps(gate, sort_keys=True))
        return 0 if gate["eligible"] else 2
    with tempfile.TemporaryDirectory(prefix="baa-cost-data-") as data_dir:
        prior = os.environ.get("BAA_DATA_DIR")
        os.environ["BAA_DATA_DIR"] = data_dir
        try:
            result = run_offline_baseline(args.manifest, trials=args.trials, output_dir=args.output_dir)
        finally:
            if prior is None:
                os.environ.pop("BAA_DATA_DIR", None)
            else:
                os.environ["BAA_DATA_DIR"] = prior
    print(json.dumps({"case_count": result["summary"]["case_count"], "trial_count": len(result["trials"]),
                      "quality_passed": result["summary"]["quality_gate_passed_case_count"],
                      "actual_model_gate": result["gate"]["eligibility"]}, sort_keys=True))
    return 0 if result["summary"]["quality_gate_passed_case_count"] == result["summary"]["case_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
