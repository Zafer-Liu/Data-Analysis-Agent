"""Used-definition snapshots and conservative, local semantic readiness checks.

These records describe business meaning, not execution authorization. Schema
validation never runs a declaration, SQL query, embedding or model call.
"""
from __future__ import annotations

import json
from collections import defaultdict

from Function.Knowledge.definition_history import canonical_json, content_hash
from Function.Knowledge.ontology_semantics import build_nodes, expand_dependencies, target_path
from Function.Knowledge.ontology_store import OntologyStore


_CONTRACT_FIELDS = ("grain", "time_window", "unit", "filters", "deduplication", "null_policy")


def _copy(value):
    return json.loads(canonical_json(value))


def _issue(code, path, message, *, state="incomplete", **extra):
    return {"code": code, "path": path, "message": message, "state": state, **extra}


def _items(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        raise ValueError("used_definitions must be a definition, search result or list")
    if any(key in value for key in ("ontologies", "metrics", "rules")) and "id" not in value:
        return [*value.get("ontologies", []),
                *({**row, "kind": "metric"} for row in value.get("metrics", [])),
                *({**row, "kind": "rule"} for row in value.get("rules", []))]
    return [value]


def _ref(oid, revision, path):
    return (str(oid), str(revision), str(path))


def _expression_refs(expression):
    """Recognize a single scalar SQL expression; unknown functions stay explicit."""
    import sqlglot
    from sqlglot import exp

    nodes = sqlglot.parse(str(expression))
    if len(nodes) != 1 or nodes[0] is None:
        raise ValueError("expected one SQL expression")
    node = nodes[0]
    if isinstance(node, (exp.Query, exp.Command, exp.DDL, exp.DML)) or node.find(exp.Subquery):
        raise ValueError("queries and commands are not scalar metric expressions")
    if any(isinstance(item, exp.Anonymous) for item in node.walk()):
        raise ValueError("function semantics are unsupported")
    if any(column.is_star for column in node.find_all(exp.Column)):
        raise ValueError("qualified wildcard columns are unsupported")
    if any(star.parent is None or not isinstance(star.parent, exp.Count) for star in node.find_all(exp.Star)):
        raise ValueError("wildcards are only supported inside COUNT")
    return [(column.table, column.name) for column in node.find_all(exp.Column)]


def build_semantic_context(kb, used_definitions, sources=None, *, previous_context=None) -> dict:
    """Pin only used definitions to server revisions and validate their dependencies.

    Accepts OntologyStore.search results, OntologyStore.definition pages, ordinary
    metric/rule rows, or a knowledge-search dict. Pass successive precise pages
    with previous_context to close omitted dependencies without switching to the
    latest revision. Model-supplied definitions are compared to server content.
    """
    store = kb.ontologies
    previous = previous_context or {}
    previous_issues = []
    if previous:
        identity = {key: previous.get(key, []) for key in ("used_definitions", "required_definitions", "definition_pages")}
        if content_hash(identity) != previous.get("snapshot_hash"):
            previous = {}
            previous_issues.append(_issue("snapshot_mismatch", "previous_context", "The previous semantic snapshot failed hash verification."))
    used = {_ref(row.get("ontology_id", ""), row["revision_id"], row["path"]): _copy(row)
            for row in previous.get("used_definitions", [])}
    required = {_ref(row["ontology_id"], row["revision_id"], row["path"])
                for row in previous.get("required_definitions", [])}
    pages = {_ref(row["ontology_id"], row["revision_id"], row["path"]): _copy(row)
             for row in previous.get("definition_pages", [])}
    issues = previous_issues
    # Reconstruct immutable server records from their IDs, never from a caller's snapshot.
    records = {}

    def record_for(oid, revision=""):
        key = (oid, revision)
        if key not in records:
            try:
                record = store.get(oid, revision_id=revision)
                if record and not revision:
                    record = store.get(oid, revision_id=record["revision_id"])
                records[key] = record
            except (ValueError, KeyError, TypeError):
                records[key] = None
        return records[key]

    def snapshot(record, node):
        return {"ontology_id": record["id"], "revision_id": record["revision_id"],
                "version": record["version"], "content_hash": record["content_hash"],
                "binding_hash": record["binding_hash"], "binding_snapshot": _copy(record["binding"]),
                "business_namespace": record.get("business_namespace", ""),
                "category_id": record["category_id"], "path": node["path"], "kind": node["kind"],
                "definition_hash": content_hash(node["payload"]), "definition": _copy(node["payload"]),
                "metric_contract_id": f"ontology:{record['id']}:{node['path']}"}

    def require(record, paths):
        nodes = build_nodes(record["document"])
        by_path = {row["path"]: row for row in nodes}
        seeds = [by_path[path] for path in paths if path in by_path]
        chosen, plan = expand_dependencies(record["document"], nodes, seeds, "")
        dependencies = {row["path"] for row in chosen
                        if row["kind"] != "dimensions" or row["path"] in paths}
        dependencies.update(paths)
        for path in dependencies:
            required.add(_ref(record["id"], record["revision_id"], path))
        return by_path, plan

    # Revalidate old snapshots against the same revision. A retention purge is explicit.
    for key, row in list(used.items()):
        oid, revision, path = key
        if oid:
            record = record_for(oid, revision)
            node = next((item for item in build_nodes(record["document"]) if item["path"] == path), None) if record else None
            if node is None:
                issues.append(_issue("revision_unavailable", path, "The pinned definition revision is unavailable."))
                used.pop(key)
            else:
                used[key] = snapshot(record, node)
        else:
            saved = kb.definition_revision(row["kind"], row["definition_id"], revision)
            if saved is None:
                used.pop(key)
                issues.append(_issue("revision_unavailable", path, "The pinned ordinary definition is unavailable."))
            else:
                row["definition"] = saved

    for item in _items(used_definitions):
        if not isinstance(item, dict):
            raise ValueError("every used definition must be an object")
        kind = item.get("kind")
        if kind in ("metric", "rule") or (("sql_template" in item or "rule_id" in item) and "entries" not in item):
            kind = kind or ("rule" if "rule_id" in item else "metric")
            did = int(item.get("definition_id", item.get("id", 0)))
            current = kb.get_metric_by_id(did) if kind == "metric" else kb.get_rule_by_id(did)
            revision = item.get("revision_id") or (current or {}).get("revision_id", "")
            saved = kb.definition_revision(kind, did, revision) if revision else None
            if not saved:
                issues.append(_issue("revision_unavailable", f"{kind}:{did}", "The ordinary definition revision is unavailable."))
                continue
            path = f"{kind}:{did}"
            used[_ref("", revision, path)] = {
                "ontology_id": "", "definition_id": did, "revision_id": revision,
                "path": path, "kind": kind, "content_hash": saved["content_hash"],
                "definition_hash": saved["content_hash"], "definition": saved,
                "business_namespace": saved["business_namespace"], "category_id": saved["category_id"],
                "metric_contract_id": saved["metric_contract_id"], "binding_snapshot": {},
            }
            continue
        oid = str(item.get("ontology_id") or item.get("id") or "")
        revision = str(item.get("revision_id") or "")
        record = record_for(oid, revision)
        if record is None:
            issues.append(_issue("revision_unavailable", oid, "The ontology revision is unavailable."))
            continue
        if not revision and not (record["enabled"] and record.get("namespace_enabled", True)):
            issues.append(_issue("definition_disabled", oid, "The current definition or namespace is disabled."))
        paths = [str(item["path"])] if "path" in item else [str(row["path"]) for row in item.get("entries", [])]
        supplied_required = list(item.get("semantic_context", {}).get("required_paths", []))
        # Budget hints without a plan still carry exact paths that need follow-up.
        if item.get("requires_followup"):
            supplied_required.extend(item.get("omitted_paths", []))
        dependency_paths = set(supplied_required if supplied_required else paths)
        by_path, dependency_plan = require(record, dependency_paths)
        for entity in dependency_plan["disconnected_entities"]:
            issues.append(_issue("relation_missing", oid, f"No declared relation connects required entity {entity}."))
        rows = item.get("entries") if "entries" in item else [item]
        for row in rows:
            path = str(row.get("path") or "")
            node = by_path.get(path)
            if node is None:
                issues.append(_issue("definition_missing", path, "The referenced definition path does not exist."))
                continue
            key = _ref(oid, record["revision_id"], path)
            definition = row.get("definition")
            if definition is None and "content" in row and "offset" not in row:
                try:
                    definition = json.loads(row["content"])
                except (ValueError, TypeError):
                    pass
            if definition is not None:
                if content_hash(definition) != content_hash(node["payload"]):
                    issues.append(_issue("definition_mismatch", path, "Supplied content differs from the pinned revision."))
                    continue
                used[key] = snapshot(record, node)
                pages.pop(key, None)
            elif "offset" in row:
                text = canonical_json(node["payload"])
                start, content = int(row["offset"]), str(row.get("content") or "")
                if (start < 0 or not content or text[start:start + len(content)] != content
                        or row.get("total_chars") != len(text)
                        or row.get("definition_hash") != content_hash(node["payload"])):
                    issues.append(_issue("definition_page_mismatch", path, "Definition page does not match its pinned revision."))
                    continue
                partial = pages.setdefault(key, {"ontology_id": oid, "revision_id": record["revision_id"],
                                                "path": path, "total_chars": len(text), "ranges": []})
                intervals = sorted([*partial["ranges"], [start, start + len(content)]])
                merged = []
                for left, right in intervals:
                    if merged and left <= merged[-1][1]:
                        merged[-1][1] = max(merged[-1][1], right)
                    else:
                        merged.append([left, right])
                partial["ranges"] = merged
                end = merged[0][1] if merged[0][0] == 0 else 0
                partial["next_offset"] = end if end < len(text) else None
                if end == len(text):
                    used[key] = snapshot(record, node)
                    pages.pop(key, None)
            else:
                issues.append(_issue("definition_unread", path, "Read the exact definition before using it."))

    # Combine separate exact reads before computing JOIN closure. A metric and
    # dimension supplied as separate pages still require their connecting relation.
    combined = defaultdict(set)
    for oid, revision, path in required:
        combined[(oid, revision)].add(path)
    for (oid, revision), paths in combined.items():
        record = record_for(oid, revision)
        if record:
            _, plan = require(record, paths)
            for entity in plan["disconnected_entities"]:
                issues.append(_issue("relation_missing", oid, f"No declared relation connects required entity {entity}."))
    # A complete entity read already contains its property definitions. Record
    # only required child paths; this does not fetch any omitted parent content.
    for key in sorted(required - set(used)):
        oid, revision, path = key
        if ".properties." in path:
            parent_path = path.split(".properties.", 1)[0]
            if _ref(oid, revision, parent_path) in used:
                record = record_for(oid, revision)
                node = next((row for row in build_nodes(record["document"]) if row["path"] == path), None) if record else None
                if node is not None:
                    used[key] = snapshot(record, node)
    # Preserve unresolved revision failures until their references can be loaded again.
    for oid, revision, path in required:
        if record_for(oid, revision) is None:
            issues.append(_issue("revision_unavailable", path, "The pinned revision is unavailable or expired."))
    result = {"version": 1, "used_definitions": [used[key] for key in sorted(used)],
              "required_definitions": [{"ontology_id": oid, "revision_id": revision, "path": path}
                                       for oid, revision, path in sorted(required)],
              "definition_pages": [pages[key] for key in sorted(pages)], "build_issues": issues,
              "security_status": "advisory_only"}
    identity = {key: result[key] for key in ("used_definitions", "required_definitions", "definition_pages")}
    result["snapshot_hash"] = content_hash(identity)
    result["context_id"] = "sc_" + result["snapshot_hash"][:32]
    return validate_semantic_context(result, sources)


def validate_semantic_context(context, sources=None) -> dict:
    """Return a detached readiness record, including current schema drift.

    Only required tables/fields are checked. Empty/missing column metadata is
    unknown, never proof that a binding is valid. Historical definitions remain
    pinned when their live ontology is replaced, disabled or deleted.
    """
    result = _copy(context)
    issues = _copy(result.get("build_issues", []))
    identity = {key: result.get(key, []) for key in ("used_definitions", "required_definitions", "definition_pages")}
    if content_hash(identity) != result.get("snapshot_hash"):
        issues.append(_issue("snapshot_mismatch", "snapshot_hash", "The semantic snapshot hash is missing or invalid."))
    used = result.get("used_definitions", [])
    if not used:
        issues.append(_issue("definitions_missing", "used_definitions", "No complete used definitions are available."))
    present = {_ref(row.get("ontology_id", ""), row["revision_id"], row["path"]) for row in used}
    required_keys = {_ref(row["ontology_id"], row["revision_id"], row["path"])
                     for row in result.get("required_definitions", [])}
    for row in result.get("required_definitions", []):
        if _ref(row["ontology_id"], row["revision_id"], row["path"]) not in present:
            issues.append(_issue("dependency_unread", row["path"], "A required definition needs an exact read.",
                                 ontology_id=row["ontology_id"], revision_id=row["revision_id"]))
    groups = defaultdict(list)
    ordinary_names = defaultdict(set)
    for row in used:
        oid = row.get("ontology_id", "")
        if oid:
            groups[(oid, row["revision_id"])].append(row)
        else:
            definition = row["definition"]
            name = definition.get("name", definition.get("rule_id", ""))
            ordinary_names[(row["kind"], name)].add(row.get("category_id"))
            issues.append(_issue("ordinary_semantics_unverified", row["path"],
                                 "An ordinary text definition has no verified physical binding or expression contract."))
    for (_, name), namespaces in ordinary_names.items():
        if len(namespaces) > 1:
            issues.append(_issue("namespace_ambiguous", name, "Select the business namespace for this name.", state="needs_clarification"))
    revisions_by_ontology = defaultdict(set)
    metric_owners = defaultdict(set)
    for (oid, revision), rows in groups.items():
        revisions_by_ontology[oid].add(revision)
        for row in rows:
            if row["kind"] == "metrics":
                metric_owners[row["path"]].add(oid)
    for oid, revisions in revisions_by_ontology.items():
        if len(revisions) > 1:
            issues.append(_issue("revision_conflict", oid, "Select one ontology revision for this analysis.", state="needs_clarification"))
    for path, owners in metric_owners.items():
        if len(owners) > 1:
            issues.append(_issue("namespace_ambiguous", path, "Select the ontology/business namespace for this metric name.",
                                 state="needs_clarification"))
    required_tables, required_columns, source_ids = [], [], set()
    for (oid, revision), rows in groups.items():
        by_path = {row["path"]: row for row in rows}
        binding = rows[0].get("binding_snapshot") or {}
        try:
            source = OntologyStore._bound_source(binding, sources or [])
        except (KeyError, TypeError):
            source = None
        if source:
            source_ids.add(str(source["source_id"]))
        else:
            issues.append(_issue("schema_unavailable" if sources is None else "source_unavailable", oid,
                                 "The bound source and its schema must be supplied for validation."))
        sid = str((source or {}).get("source_id") or binding.get("source_id") or "")
        entities = {path.split(".", 1)[1]: row["definition"] for path, row in by_path.items()
                    if row["kind"] == "entities"}
        needed = defaultdict(set)

        def table_for(entity, path):
            value = entities.get(entity)
            if value is None:
                issues.append(_issue("entity_unread", path, f"Entity {entity} requires an exact definition read."))
                return ""
            physical = value.get("source") or {}
            table = physical.get("table") or physical.get("view") or ""
            if not table:
                issues.append(_issue("physical_source_missing", path, f"Entity {entity} has no physical source."))
            else:
                needed[table]  # COUNT(*) still depends on a table.
            return table

        def add_expression(expression, entity, path):
            try:
                references = _expression_refs(expression)
            except Exception as exc:
                # Parser/dialect failures must be visible rather than an empty dependency set.
                issues.append(_issue("expression_unsupported", path, f"Expression is not verified: {type(exc).__name__}."))
                return
            base = table_for(entity, path)
            for qualifier, column in references:
                table = base
                if qualifier and qualifier not in {entity, base}:
                    if qualifier in entities:
                        table = table_for(qualifier, path)
                    elif qualifier in binding.get("mapping", {}):
                        table = qualifier
                    else:
                        issues.append(_issue("expression_source_unknown", path, f"Unknown expression qualifier {qualifier}."))
                        continue
                if table:
                    needed[table].add(column)

        for row in rows:
            path, kind, value = row["path"], row["kind"], row["definition"]
            if kind == "dimensions" and _ref(oid, revision, path) not in required_keys:
                continue  # An available dimension is informational until selected.
            if kind == "entities":
                table = table_for(path.split(".", 1)[1], path)
                primary = (value.get("source") or {}).get("primary_key")
                if table and primary:
                    needed[table].add(primary)
            elif kind == "properties":
                table = table_for(value["entity"], path)
                if table:
                    needed[table].add(value["property"])
            elif kind == "metrics":
                add_expression(value.get("expression", ""), value.get("entity", ""), path)
                for expression in value.get("filters", []):
                    add_expression(expression, value.get("entity", ""), path + ".filters")
                contract = {key: value.get(key) for key in _CONTRACT_FIELDS if key in value}
                missing = [key for key in _CONTRACT_FIELDS if key not in contract or contract[key] is None or contract[key] == ""]
                if missing:
                    issues.append(_issue("metric_contract_unspecified", path, "Clarify metric contract: " + ", ".join(missing),
                                         state="needs_clarification", fields=missing))
            elif kind == "dimensions":
                table = table_for(value["entity"], path)
                if table:
                    needed[table].add(value["property"])
            elif kind == "relations":
                join = value.get("join") or {}
                for side in ("from", "to"):
                    if join.get(side + "_table"):
                        needed[join[side + "_table"]].add(join[side + "_column"])
                if join.get("via_table"):
                    needed[join["via_table"]].update([join["via_from_column"], join["via_to_column"]])
                if value.get("fanout") or value.get("cardinality") in ("many_to_many", "one_to_many"):
                    issues.append(_issue("join_grain_unverified", path, "Confirm aggregation grain across a fanout relation.",
                                         state="needs_clarification"))
            elif kind == "query_surface":
                name = path.split(".", 1)[1]
                needed[name].update(value.get("columns", []))
            elif kind == "synonyms" and value.get("target"):
                if target_path(value["target"]) not in by_path:
                    issues.append(_issue("alias_target_unread", path, "The alias target definition is unread."))
                if value.get("scope"):
                    issues.append(_issue("scope_unverified", path, "Resolve the alias scope using trusted context.", state="needs_clarification"))
            elif kind == "intents":
                missing = [slot for slot in value.get("required_slots", []) if slot not in value.get("defaults", {})]
                if missing or value.get("clarification"):
                    issues.append(_issue("intent_clarification", path, "Required intent slots or clarification remain unresolved.",
                                         state="needs_clarification", slots=missing))
            elif kind == "quality_rules":
                issues.append(_issue("quality_rule_unverified", path, "The declared quality rule has not been executed."))
        tables = (source or {}).get("tables") or {}
        for logical, columns in sorted(needed.items()):
            target = binding.get("mapping", {}).get(logical, logical)
            required_tables.append({"ontology_id": oid, "revision_id": revision, "source_id": sid,
                                    "logical_table": logical, "table": target})
            if not target:
                issues.append(_issue("table_unmapped", logical, "The required table is explicitly unmapped."))
                continue
            if source and target not in tables:
                issues.append(_issue("table_missing", logical, f"Required table {target} is absent from the current schema."))
            actual = tables.get(target)
            if source and target in tables and not actual:
                issues.append(_issue("schema_unavailable", logical, f"Column metadata for {target} is unavailable."))
            for column in sorted(columns):
                destination = binding.get("column_mapping", {}).get(logical, {}).get(column, column)
                required_columns.append({"ontology_id": oid, "revision_id": revision, "source_id": sid,
                                         "logical_table": logical, "table": target,
                                         "logical_column": column, "column": destination})
                if not destination:
                    issues.append(_issue("column_unmapped", f"{logical}.{column}", "The required column is explicitly unmapped."))
                elif actual and destination not in actual:
                    issues.append(_issue("column_missing", f"{logical}.{column}", f"Required column {target}.{destination} is absent."))
    # Derived metric contracts do not change the pinned definition snapshot hash.
    result["required_tables"], result["required_columns"] = required_tables, required_columns
    result["source_ids"] = sorted(source_ids)
    result["issues"] = list({canonical_json(issue): issue for issue in issues}.values())
    result["state"] = ("incomplete" if any(issue["state"] == "incomplete" for issue in issues)
                       else "needs_clarification" if issues else "ready")
    return result
