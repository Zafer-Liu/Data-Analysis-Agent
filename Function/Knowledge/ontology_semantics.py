"""Typed ontology projections and dependency closure; never executes declarations."""
from __future__ import annotations

import heapq
import json
import re

PROJECTION_VERSION = 2
_INTENT_TERMS = {
    "aggregate_metric": ("统计", "汇总", "平均", "均分", "及格率", "优秀率", "GPA"),
    "detail_query": ("明细", "个人", "我的", "多少分"),
    "ranking": ("排名", "名次", "第一", "前十", "前10"),
    "trend": ("趋势", "变化", "按学期", "按月", "按年"),
    "comparison": ("对比", "比较", "相比"),
    "distribution": ("分布", "分数段", "区间"),
    "transcript_report": ("成绩单", "成绩证明"),
    "anomaly_scan": ("异常", "连续", "需要补考"),
}


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def target_path(target: str) -> str:
    if target.startswith("Metric."):
        return "metrics." + target[7:]
    if "." in target:
        entity, prop = target.split(".", 1)
        return f"entities.{entity}.properties.{prop}"
    return "entities." + target


def build_nodes(document: dict) -> list[dict]:
    """Preserve attribute aliases and alias scopes instead of flattening them away."""
    nodes, aliases = [], {}
    for name, value in document.get("synonyms", {}).items():
        aliases.setdefault(target_path(value["target"]), []).append(name)

    def add(path, kind, label, payload, names=()):
        names = list(dict.fromkeys(str(name) for name in [label, *names, *aliases.get(path, [])] if name))
        nodes.append({"path": path, "kind": kind, "label": label, "names": names,
                      "payload": payload, "content": " ".join(names) + "\n" + json_text(payload)})

    for kind in ("entities", "metrics", "dimensions", "intents", "query_surface"):
        for key, value in document.get(kind, {}).items():
            names = [key, *value.get("synonyms", []), *value.get("aliases", [])]
            if kind == "intents":
                names.extend(_INTENT_TERMS.get(key, ()))
            add(f"{kind}.{key}", kind, value.get("label") or key, value, names)
            if kind == "entities":
                for prop, definition in value.get("properties", {}).items():
                    add(f"entities.{key}.properties.{prop}", "properties",
                        definition.get("label") or prop,
                        {**definition, "entity": key, "property": prop},
                        [prop, *definition.get("synonyms", []), *definition.get("aliases", []),
                         *definition.get("value_map", {}).values()])
    for name, value in document.get("synonyms", {}).items():
        add(f"synonyms.{name}", "synonyms", name, value, [name])
    for index, value in enumerate(document.get("relations", [])):
        add(f"relations.{index}", "relations", value["name"], value, [value["name"]])
    for index, value in enumerate(document.get("quality_rules", [])):
        add(f"quality_rules.{index}", "quality_rules", value["name"], value, [value["name"]])
    if document.get("domain"):
        add("domain", "domain", document["domain"]["name"], document["domain"], ["业务域", "默认口径"])
    if document.get("security"):
        add("security", "security", "权限声明", document["security"], ["权限", "授权", "可见范围"])
    return nodes


def name_matches(question: str, names: list[str]) -> list[str]:
    q = question.casefold()
    matched = []
    for name in names:
        term = name.casefold()
        if not term:
            continue
        if re.fullmatch(r"[a-z0-9_]+", term):
            found = re.search(rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])", q)
        else:
            found = len(term) >= 2 and term in q or bool(re.fullmatch(r"[一-鿿]", term) and term in q)
            if not found:
                for prefix in ("我的", "我教的"):
                    if term.startswith(prefix) and len(term) > len(prefix):
                        found = re.search(re.escape(prefix) + r".{0,24}" + re.escape(term[len(prefix):]), q)
        if found:
            matched.append(name)
    return matched


def choose_seeds(question: str, ranked: list[tuple[float, dict]]) -> list[dict]:
    # A field-path follow-up must be able to retrieve a previously omitted entry.
    exact = [row for _, row in ranked if row["path"].casefold() in question.casefold()]
    if exact:
        return exact[:4]
    quotas = {"metrics": 2, "properties": 3, "entities": 2, "dimensions": 3,
              "intents": 2, "synonyms": 3, "query_surface": 2, "relations": 2,
              "security": 1, "quality_rules": 1, "domain": 1}
    exact_by_kind = {}
    metric_terms = set()
    alias_targets, named_entities = set(), set()
    for _, row in ranked:
        terms = name_matches(question, row["names"])
        if terms:
            exact_by_kind.setdefault(row["kind"], set()).add(row["path"])
            if row["kind"] == "metrics":
                metric_terms.update(term.casefold() for term in terms)
            elif row["kind"] == "synonyms":
                alias_targets.add(target_path(row["payload"]["target"]))
            elif row["kind"] == "entities":
                named_entities.add(row["path"].split(".", 1)[1])
    detail_requested = "intents.detail_query" in exact_by_kind.get("intents", set())
    seeds, counts = [], {}
    for _, row in ranked:
        kind = row["kind"]
        if kind in {"metrics", "properties", "intents"} and exact_by_kind.get(kind) and row["path"] not in exact_by_kind[kind]:
            continue
        if kind == "metrics" and detail_requested and not exact_by_kind.get("metrics"):
            continue
        if (kind == "properties" and row["payload"]["property"].casefold() in metric_terms
                and row["path"] not in alias_targets and row["payload"]["entity"] not in named_entities):
            continue
        if counts.get(kind, 0) >= quotas.get(kind, 1):
            continue
        # Suppress generic aliases when a longer matched alias identifies a field.
        if kind == "synonyms":
            alias = row["label"]
            if any(other["kind"] == "synonyms" and len(other["label"]) > len(alias)
                   and alias in other["label"] and other["label"] in question
                   for _, other in ranked):
                continue
        seeds.append(row)
        counts[kind] = counts.get(kind, 0) + 1
        if len(seeds) >= 12:
            break
    return seeds


def _expression_columns(metric: dict) -> set[str]:
    """Read identifiers from the expression AST, without evaluating the SQL."""
    try:
        import sqlglot
        import sqlglot.expressions as exp
        columns = set()
        for expression in [metric.get("expression", ""), *metric.get("filters", [])]:
            node = sqlglot.parse_one(expression)
            columns.update(column.name for column in node.find_all(exp.Column))
        return columns
    except Exception:
        # A dialect-specific expression can still be preserved verbatim as knowledge.
        return set()


def expand_dependencies(document: dict, rows: list[dict], seeds: list[dict], question: str) -> tuple[list[dict], dict]:
    by_path = {row["path"]: row for row in rows}
    selected = {row["path"] for row in seeds}
    requested_entities, metric_ids, requested_dimensions = set(), [], set()
    explicit_relations = set()
    queue = list(seeds)
    visited = set()
    bare_entities, explicit_properties = set(), set()
    for row in seeds:
        if row["kind"] == "entities":
            bare_entities.add(row["path"].split(".", 1)[1])
        elif row["kind"] == "properties":
            explicit_properties.add(row["payload"]["entity"])
        elif row["kind"] == "synonyms":
            target = row["payload"]["target"]
            if "." in target:
                explicit_properties.add(target.split(".", 1)[0])
            else:
                bare_entities.add(target)
    for row in rows:
        if row["kind"] == "synonyms" and row["payload"].get("default"):
            target = row["payload"]["target"]
            entity = target.split(".", 1)[0]
            if entity in bare_entities and entity not in explicit_properties:
                selected.add(row["path"])
                queue.append(row)

    def include(path):
        if path in by_path and path not in selected:
            selected.add(path)
            queue.append(by_path[path])

    while queue:
        row = queue.pop(0)
        path, kind, value = row["path"], row["kind"], row["payload"]
        if path in visited:
            continue
        visited.add(path)
        if kind == "synonyms":
            include(target_path(value["target"]))
        elif kind == "metrics":
            key = path.split(".", 1)[1]
            if key not in metric_ids:
                metric_ids.append(key)
            entity = value.get("entity")
            if entity:
                requested_entities.add(entity)
                include(f"entities.{entity}")
                for column in _expression_columns(value):
                    include(f"entities.{entity}.properties.{column}")
            # Every permitted dimension is defined, but only requested dimensions
            # add JOIN requirements. Listing available dimensions must not add JOINs.
            for name in value.get("dimensions", []):
                dim_path = f"dimensions.{name}"
                if dim_path in by_path:
                    selected.add(dim_path)
        elif kind == "dimensions":
            key = path.split(".", 1)[1]
            requested_dimensions.add(key)
            entity = value["entity"]
            requested_entities.add(entity)
            include(f"entities.{entity}")
            include(f"entities.{entity}.properties.{value['property']}")
        elif kind == "properties":
            entity = value["entity"]
            requested_entities.add(entity)
            include(f"entities.{entity}")
            reference = value.get("reference")
            if reference:
                requested_entities.add(reference)
                include(f"entities.{reference}")
                physical = document["entities"][entity].get("source", {})
                table = physical.get("table") or physical.get("view")
                for index, rel in enumerate(document.get("relations", [])):
                    join = rel["join"]
                    if (rel["from"] == entity and rel["to"] == reference
                            and join["from_table"] == table and join["from_column"] == value["property"]):
                        include(f"relations.{index}")
                        explicit_relations.add(f"relations.{index}")
        elif kind == "entities":
            requested_entities.add(path.split(".", 1)[1])
        elif kind == "relations":
            requested_entities.update((value["from"], value["to"]))
            include(f"entities.{value['from']}")
            include(f"entities.{value['to']}")
            if row in seeds:
                explicit_relations.add(path)

    # All declared business policies are attached independently of seed ranking.
    selected.update(row["path"] for row in rows if row["kind"] in {"domain", "security", "quality_rules"})
    graph = {}
    for row in rows:
        if row["kind"] != "relations":
            continue
        rel = row["payload"]
        weight = 1.0 + (0.5 if rel.get("fanout") else 0) + (0.75 if rel.get("cardinality") == "many_to_many" else 0)
        if row["path"] in explicit_relations:
            weight = 0.25
        graph.setdefault(rel["from"], []).append((rel["to"], row["path"], weight))
        graph.setdefault(rel["to"], []).append((rel["from"], row["path"], weight))
    roots = [document["metrics"][key].get("entity") for key in metric_ids if document["metrics"][key].get("entity")]
    root = next(iter(roots), next(iter(sorted(requested_entities)), None))
    join_paths, disconnected = [], []
    for target in sorted(requested_entities):
        if not root or target == root:
            continue
        pending, best = [(0.0, root, [])], {}
        found = None
        while pending:
            cost, current, path = heapq.heappop(pending)
            if current in best:
                continue
            best[current] = cost
            if current == target:
                found = path
                break
            for other, relation, weight in graph.get(current, []):
                if other not in best:
                    heapq.heappush(pending, (cost + weight, other, [*path, relation]))
        if found is None:
            disconnected.append(target)
            continue
        selected.update(found)
        join_paths.append({"from": root, "to": target, "relations": found})
        for path in found:
            relation = by_path[path]["payload"]
            selected.update((f"entities.{relation['from']}", f"entities.{relation['to']}"))

    surfaces = []
    entity_sources = set()
    for entity in requested_entities:
        source = document["entities"][entity].get("source", {})
        entity_sources.update(value for key, value in source.items() if key in {"table", "view"} and value)
    metric_columns = set().union(*(_expression_columns(document["metrics"][key]) for key in metric_ids)) if metric_ids else set()
    for name, surface in document.get("query_surface", {}).items():
        physical = surface.get("source_view") or surface.get("source_table") or surface.get("table") or surface.get("view")
        covers = metric_columns and metric_columns.issubset(set(surface.get("columns", [])))
        if physical in entity_sources or covers or f"query_surface.{name}" in selected:
            selected.add(f"query_surface.{name}")
            surfaces.append({"name": name, "declared_source": physical or "",
                             "preferred_for_entity": physical in entity_sources,
                             "covers_metric_columns": bool(covers)})
    # A request about a particular role must carry its allowed query surfaces.
    if "security" in selected:
        for role in document.get("security", {}).get("roles", {}).values():
            for name in role.get("allowed_tables", []):
                if f"query_surface.{name}" in by_path and (not metric_ids or any(item["name"] == name for item in surfaces)):
                    selected.add(f"query_surface.{name}")

    scope_aliases = [row["payload"] for row in rows
                    if row["path"] in selected and row["kind"] == "synonyms" and row["payload"].get("scope")]
    selected_intents = [row for row in rows if row["path"] in selected and row["kind"] == "intents"]
    plan = {
        "metrics": metric_ids, "requested_dimensions": sorted(requested_dimensions),
        "join_paths": join_paths, "disconnected_entities": disconnected,
        "query_surface_candidates": surfaces, "scoped_aliases": scope_aliases,
        "intents": [{"id": row["path"].split(".", 1)[1],
                     "required_slots": row["payload"].get("required_slots", []),
                     "optional_slots": row["payload"].get("optional_slots", []),
                     "defaults": row["payload"].get("defaults", {}),
                     "clarification": row["payload"].get("clarification", [])} for row in selected_intents],
        "domain_defaults": {key: document.get("domain", {}).get(key)
                            for key in ("timezone", "default_limit") if key in document.get("domain", {})},
        "authorization": {"status": "advisory_only", "fail_closed_requested": bool(document.get("security", {}).get("fail_closed")),
                          "requires_trusted_identity": bool(document.get("security", {}).get("roles"))},
    }
    priority = {"domain": 0, "security": 1, "quality_rules": 2, "metrics": 3, "synonyms": 4,
                "properties": 5, "intents": 6, "query_surface": 7, "relations": 8, "dimensions": 9, "entities": 10}
    chosen = sorted((row for row in rows if row["path"] in selected),
                    key=lambda row: (priority.get(row["kind"], 20), row["path"]))
    return chosen, plan
