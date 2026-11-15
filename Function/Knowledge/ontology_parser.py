# -*- coding: utf-8 -*-
"""Bounded, deterministic ontology import; declarations never execute SQL or policy."""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from yaml.events import AliasEvent
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

__all__ = ["OntologyValidationError", "parse_ontology", "validate_ontology"]

_MAX_BYTES = 256 * 1024
_MAX_DEPTH = 32
_MAX_NODES = 20_000
_SECTION_LIMITS = {
    "entities": 256, "relations": 1024, "dimensions": 1024,
    "metrics": 1024, "query_surface": 256, "synonyms": 4096,
    "intents": 256, "quality_rules": 1024,
}
_PROPERTY_TYPES = {
    "string", "integer", "decimal", "number", "float", "boolean", "enum",
    "reference", "date", "datetime", "time", "json",
}
_CARDINALITIES = {"one_to_one", "one_to_many", "many_to_one", "many_to_many"}
_MAX_DEFAULT_LIMIT = 10_000
_TIMEZONE = re.compile(r"[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*\Z")
_TIMEZONE_AREAS = {
    "Africa", "America", "Antarctica", "Arctic", "Asia", "Atlantic", "Australia",
    "Brazil", "Canada", "Chile", "Etc", "Europe", "Indian", "Mexico", "Pacific", "US",
}
_TIMEZONE_ALIASES = {
    "UTC", "GMT", "UCT", "Universal", "Zulu", "Greenwich", "GMT0",
    "CET", "EET", "MET", "WET", "EST", "MST", "HST",
    "EST5EDT", "CST6CDT", "MST7MDT", "PST8PDT",
}
_RUNTIME_INTENT_SLOTS = {
    "metric", "limit", "time_dimension", "compare_dimension", "condition",
    "score_status", "attempt_scope", "ranking_scope", "transcript_status",
}
_VERSION = re.compile(r"1(?:\.\d+){0,2}(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\Z")


class OntologyValidationError(ValueError):
    """Import failure with field-addressable, user-facing validation errors."""

    def __init__(self, errors: list[dict[str, str]]):
        self.errors = errors
        super().__init__("；".join(f"{e['path']}: {e['message']}" for e in errors))


def _fail(path: str, message: str) -> OntologyValidationError:
    return OntologyValidationError([{"path": path, "message": message}])


def _field(path: str, key: str | int) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]"
    if key.isidentifier():
        return f"{path}.{key}"
    return f"{path}[{json.dumps(key, ensure_ascii=False)}]"


def _path(parts: tuple) -> str:
    result = "$"
    for part in parts:
        result = _field(result, part)
    return result


def _mapping_key(key: Any, parts: tuple) -> str:
    if isinstance(key, str):
        return key
    # Numeric term enums are valid too, not only properties with type: enum.
    enum_map = (
        len(parts) == 5 and parts[0] == "entities"
        and parts[2] == "properties" and parts[4] == "value_map"
    )
    if enum_map:
        if key is None:
            return "null"
        if type(key) is bool:
            return "true" if key else "false"
        if type(key) is int and key.bit_length() <= 4096:
            return str(key)
        if type(key) is float and math.isfinite(key):
            return str(key)
        if isinstance(key, (date, datetime, time)):
            return key.isoformat()
    raise _fail(_path(parts), "映射键必须是字符串；属性 value_map 仅允许可转换为字符串的有限标量键")


class _OntologyLoader(yaml.SafeLoader):
    """Reject aliases and resource exhaustion before constructing YAML objects."""

    def __init__(self, stream: str):
        self._ontology_depth = 0
        self._ontology_nodes = 0
        super().__init__(stream)

    def compose_node(self, parent, index):
        if self.check_event(AliasEvent):
            event = self.get_event()
            raise _fail("$", f"不支持 YAML 别名或递归引用（第 {event.start_mark.line + 1} 行）")
        self._ontology_depth += 1
        self._ontology_nodes += 1
        try:
            if self._ontology_depth > _MAX_DEPTH:
                raise _fail("$", f"嵌套深度不能超过 {_MAX_DEPTH} 层")
            if self._ontology_nodes > _MAX_NODES:
                raise _fail("$", f"节点数不能超过 {_MAX_NODES}")
            return super().compose_node(parent, index)
        finally:
            self._ontology_depth -= 1


def _yaml_value(loader: _OntologyLoader, node, parts: tuple = ()) -> Any:
    if isinstance(node, MappingNode) and node.tag == "tag:yaml.org,2002:map":
        result = {}
        for key_node, value_node in node.value:
            if not isinstance(key_node, ScalarNode):
                raise _fail(_path(parts), "不支持复合映射键")
            if key_node.tag == "tag:yaml.org,2002:merge":
                raise _fail(_path(parts), "不支持 YAML 合并键，请显式声明各字段")
            key = _mapping_key(_yaml_value(loader, key_node, parts), parts)
            if key in result:
                raise _fail(_path((*parts, key)), "映射键重复（含枚举键转换后的重复）")
            result[key] = _yaml_value(loader, value_node, (*parts, key))
        return result
    if isinstance(node, SequenceNode) and node.tag == "tag:yaml.org,2002:seq":
        return [_yaml_value(loader, child, (*parts, i)) for i, child in enumerate(node.value)]
    if isinstance(node, ScalarNode):
        try:
            return loader.construct_object(node)
        except (yaml.YAMLError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
            raise _fail(_path(parts), "无效或不支持的 YAML 标量") from exc
    raise _fail(_path(parts), "仅支持普通 YAML 映射、列表和安全标量")


def _json_copy(payload: dict) -> dict:
    """Detach the caller's graph, reject aliases, and normalize only safe scalars."""
    seen: set[int] = set()
    nodes = 0
    text_bytes = 0

    def count_text(value: str, parts: tuple) -> None:
        nonlocal text_bytes
        if len(value) > _MAX_BYTES:
            raise _fail(_path(parts), "文本超过 256 KiB 限制")
        try:
            text_bytes += len(value.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise _fail(_path(parts), "文本包含无效的 Unicode 字符") from exc
        if text_bytes > _MAX_BYTES:
            raise _fail("$", "本体内容超过 256 KiB 限制")

    def visit(value: Any, parts: tuple = (), depth: int = 1) -> Any:
        nonlocal nodes
        nodes += 1
        if nodes > _MAX_NODES:
            raise _fail(_path(parts), f"节点数不能超过 {_MAX_NODES}")
        if depth > _MAX_DEPTH:
            raise _fail(_path(parts), f"嵌套深度不能超过 {_MAX_DEPTH} 层")
        if isinstance(value, (dict, list)):
            if id(value) in seen:
                raise _fail(_path(parts), "不支持共享容器别名或递归结构")
            seen.add(id(value))
            if len(value) > _MAX_NODES:
                raise _fail(_path(parts), f"单个容器不能超过 {_MAX_NODES} 项")
            if isinstance(value, list):
                return [visit(item, (*parts, i), depth + 1) for i, item in enumerate(value)]
            result = {}
            for raw_key, item in value.items():
                nodes += 1
                if nodes > _MAX_NODES:
                    raise _fail(_path(parts), f"节点数不能超过 {_MAX_NODES}")
                key = _mapping_key(raw_key, parts)
                count_text(key, (*parts, key))
                if key in result:
                    raise _fail(_path((*parts, key)), "枚举键转换后重复")
                result[key] = visit(item, (*parts, key), depth + 1)
            return result
        if isinstance(value, (date, datetime, time)):
            value = value.isoformat()
        if isinstance(value, str):
            count_text(value, parts)
            return value
        if value is None or type(value) is bool:
            return value
        if type(value) is int:
            if value.bit_length() > 4096:
                raise _fail(_path(parts), "整数超出支持范围（最多 4096 位二进制数）")
            return value
        if type(value) is float and math.isfinite(value):
            return value
        raise _fail(_path(parts), "值必须可序列化为 JSON；不支持非有限数、二进制或其他对象")

    return visit(payload)


def parse_ontology(text: str) -> dict:
    """Parse one UTF-8 YAML/JSON ontology document and return its validated preview."""
    if not isinstance(text, str):
        raise _fail("$", "本体内容必须是文本")
    if len(text) > _MAX_BYTES:
        raise _fail("$", "本体文件不能超过 256 KiB（UTF-8）")
    try:
        if len(text.encode("utf-8")) > _MAX_BYTES:
            raise _fail("$", "本体文件不能超过 256 KiB（UTF-8）")
    except UnicodeEncodeError as exc:
        raise _fail("$", "文本包含无效的 Unicode 字符") from exc
    loader = None
    try:
        loader = _OntologyLoader(text)
        node = loader.get_single_node()
        payload = _yaml_value(loader, node) if node is not None else None
    except OntologyValidationError:
        raise
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f"（第 {mark.line + 1} 行，第 {mark.column + 1} 列）" if mark else ""
        problem = str(getattr(exc, "problem", "无效的 YAML 内容"))[:200]
        raise _fail("$", f"YAML 格式错误{location}：{problem}") from exc
    except (ValueError, OverflowError, RecursionError) as exc:
        raise _fail("$", "YAML 数值或结构超出支持范围") from exc
    finally:
        if loader is not None:
            loader.dispose()
    return validate_ontology(payload)


class _Validator:
    def __init__(self, data: dict):
        self.data = data
        self.errors: list[dict[str, str]] = []
        self.warnings: list[dict[str, str]] = []
        self.sections = {}
        for name, limit in _SECTION_LIMITS.items():
            value = data.get(name, [] if name in {"relations", "quality_rules"} else {})
            value = self._section(value, name)
            if len(value) > limit:
                self.error(f"$.{name}", f"最多允许 {limit} 项")
            self.sections[name] = value
        self.entities = self.sections["entities"]
        self.metrics = self.sections["metrics"]
        self.dimensions = self.sections["dimensions"]

    def _section(self, value: Any, name: str):
        if name in {"relations", "quality_rules"}:
            return self.sequence(value, f"$.{name}")
        return self.mapping(value, f"$.{name}")

    def error(self, path: str, message: str) -> None:
        if len(self.errors) < 100:
            self.errors.append({"path": path, "message": message})
        elif len(self.errors) == 100:
            self.errors.append({"path": "$", "message": "错误超过 100 项，请修正后重新导入"})

    def warn(self, path: str, message: str) -> None:
        if len(self.warnings) < 200:
            self.warnings.append({"path": path, "message": message})
        elif len(self.warnings) == 200:
            self.warnings.append({"path": "$", "message": "警告超过 200 项，其余警告已省略"})

    def mapping(self, value: Any, path: str) -> dict:
        if not isinstance(value, dict):
            self.error(path, "必须是映射对象")
            return {}
        return value

    def sequence(self, value: Any, path: str) -> list:
        if not isinstance(value, list):
            self.error(path, "必须是列表")
            return []
        return value

    def text(self, value: Any, path: str, *, empty: bool = False) -> bool:
        if not isinstance(value, str) or (not empty and not value.strip()):
            self.error(path, "必须是字符串" if empty else "必须是非空字符串")
            return False
        return True

    def name(self, value: Any, path: str) -> bool:
        if not self.text(value, path):
            return False
        if len(value) > 128 or re.search(r"[\s.\[\]\x00-\x1f\x7f]", value):
            self.error(path, "标识符最多 128 个字符，不能包含空白、点、方括号或控制字符")
            return False
        return True

    def strings(self, value: Any, path: str, *, unique: bool = False, identifiers: bool = False) -> list:
        result = self.sequence(value, path)
        seen = set()
        for i, item in enumerate(result):
            item_path = _field(path, i)
            valid = self.name(item, item_path) if identifiers else self.text(item, item_path)
            if not valid:
                continue
            if identifiers and not item.isidentifier():
                self.error(item_path, "必须是由字母、数字或下划线组成且不以数字开头的标识符")
            if unique and item in seen:
                self.error(item_path, f"重复声明：{item}")
            seen.add(item)
        return result

    def timezone(self, value: Any, path: str) -> None:
        if not self.text(value, path):
            return
        if len(value) > 128 or not _TIMEZONE.fullmatch(value):
            self.error(path, "必须是有效的 IANA 时区名称，例如 Asia/Shanghai 或 UTC")
            return
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            # Windows commonly lacks both the system database and optional tzdata.
            # In that case check safe IANA-shaped syntax and disclose the limitation.
            try:
                ZoneInfo("UTC")
            except ZoneInfoNotFoundError:
                if value not in _TIMEZONE_ALIASES and (
                    "/" not in value or value.split("/", 1)[0] not in _TIMEZONE_AREAS
                ):
                    self.error(path, "无法识别的 IANA 时区名称")
                else:
                    self.warn(path, "系统缺少时区数据库，仅检查了 IANA 名称语法，未验证该时区是否存在")
            else:
                self.error(path, f"未找到 IANA 时区：{value}")
        except ValueError:
            self.error(path, "无效的 IANA 时区名称")

    def metadata(self, item: dict, path: str) -> None:
        for key in ("label", "description", "notes", "note", "unit"):
            if key in item:
                self.text(item[key], _field(path, key), empty=key != "label")
        for key in ("synonyms", "aliases"):
            if key in item:
                self.strings(item[key], _field(path, key))

    def named(self, section: str):
        for name, raw in self.sections[section].items():
            path = _field(f"$.{section}", name)
            if section == "synonyms":
                self.text(name, path)  # Alias phrases may contain spaces or punctuation.
            else:
                self.name(name, path)
            item = self.mapping(raw, path)
            self.metadata(item, path)
            yield name, item, path

    def entity_ref(self, value: Any, path: str) -> dict:
        if self.text(value, path) and value not in self.entities:
            self.error(path, f"引用了未声明的实体：{value}")
        entity = self.entities.get(value) if isinstance(value, str) else None
        return entity if isinstance(entity, dict) else {}

    def property_ref(self, entity: dict, value: Any, path: str) -> None:
        if not self.text(value, path):
            return
        properties = entity.get("properties", {})
        if not isinstance(properties, dict) or value not in properties:
            self.error(path, f"引用了实体中未声明的属性：{value}")

    def domain(self) -> dict:
        domain = self.mapping(self.data.get("domain"), "$.domain")
        self.text(domain.get("name"), "$.domain.name")
        if "description" in domain:
            self.text(domain["description"], "$.domain.description", empty=True)
        if "timezone" in domain:
            self.timezone(domain["timezone"], "$.domain.timezone")
        version = self.data.get("version")
        if self.text(version, "$.version") and not _VERSION.fullmatch(version):
            self.error("$.version", "仅支持 1.x 版本，例如 1.0.0")
        if "default_limit" in domain and (
            type(domain["default_limit"]) is not int
            or not 1 <= domain["default_limit"] <= _MAX_DEFAULT_LIMIT
        ):
            self.error("$.domain.default_limit", f"必须是 1 到 {_MAX_DEFAULT_LIMIT} 之间的整数")
        if "policies" in domain:
            for key, declaration in self.mapping(domain["policies"], "$.domain.policies").items():
                policy_path = _field("$.domain.policies", key)
                self.text(key, policy_path)
                self.text(declaration, policy_path)
        if "database" in domain:
            database = self.mapping(domain["database"], "$.domain.database")
            for key in ("dialect", "default_path"):
                if key in database:
                    self.text(database[key], f"$.domain.database.{key}")
            if "source_views" in database:
                self.strings(database["source_views"], "$.domain.database.source_views")
            if "default_path" in database:
                self.warn("$.domain.database.default_path", "数据库路径仅作为描述保存，不会打开文件或切换数据源")
        return domain

    def entity_definitions(self) -> bool:
        meaningful = False
        for _, entity, path in self.named("entities"):
            source = {}
            if "source" in entity:
                source = self.mapping(entity["source"], f"{path}.source")
                if "table" not in source and "view" not in source:
                    self.error(f"{path}.source", "需要声明 table 或 view；概念实体可使用空 table")
                for key in ("table", "view", "primary_key", "business_key"):
                    if key in source:
                        self.text(source[key], f"{path}.source.{key}", empty=key == "table")
                if source.get("table") == "" and not source.get("view"):
                    self.warn(f"{path}.source.table", "该实体仅作为概念保存，尚未绑定物理表")
            properties = self.mapping(entity.get("properties", {}), f"{path}.properties")
            if len(properties) > 256:
                self.error(f"{path}.properties", "每个实体最多允许 256 个属性")
            meaningful |= bool(properties or source.get("table") or source.get("view") or any(
                entity.get(key) for key in ("label", "description", "synonyms", "aliases")
            ))
            for name, raw in properties.items():
                prop_path = _field(f"{path}.properties", name)
                self.name(name, prop_path)
                prop = self.mapping(raw, prop_path)
                self.metadata(prop, prop_path)
                kind = prop.get("type")
                if self.text(kind, f"{prop_path}.type") and kind not in _PROPERTY_TYPES:
                    self.error(f"{prop_path}.type", f"不支持的属性类型：{kind}")
                if "role" in prop and prop["role"] not in ("identifier", "dimension", "measure", "time"):
                    self.error(f"{prop_path}.role", "必须是 identifier、dimension、measure 或 time")
                if kind == "reference" or "reference" in prop:
                    self.entity_ref(prop.get("reference"), f"{prop_path}.reference")
                if "default_aggregation" in prop:
                    self.text(prop["default_aggregation"], f"{prop_path}.default_aggregation")
                if "allowed_aggregations" in prop:
                    allowed = self.strings(
                        prop["allowed_aggregations"], f"{prop_path}.allowed_aggregations", unique=True,
                    )
                    if not allowed:
                        self.error(f"{prop_path}.allowed_aggregations", "允许的聚合列表不能为空")
                    if isinstance(prop.get("default_aggregation"), str) and prop["default_aggregation"] not in allowed:
                        self.error(f"{prop_path}.default_aggregation", "默认聚合必须包含在 allowed_aggregations 中")
                if "value_map" in prop:
                    for key, label in self.mapping(prop["value_map"], f"{prop_path}.value_map").items():
                        self.text(label, _field(f"{prop_path}.value_map", key))
                if "range" in prop:
                    bounds = self.sequence(prop["range"], f"{prop_path}.range")
                    if len(bounds) != 2 or any(type(v) not in (int, float) for v in bounds):
                        self.error(f"{prop_path}.range", "必须是包含两个有限数值的列表")
                    elif bounds[0] > bounds[1]:
                        self.error(f"{prop_path}.range", "下界不能大于上界")
        return meaningful

    def relations(self) -> None:
        names = set()
        for i, raw in enumerate(self.sections["relations"]):
            path = f"$.relations[{i}]"
            relation = self.mapping(raw, path)
            self.metadata(relation, path)
            name = relation.get("name")
            if self.name(name, f"{path}.name"):
                if name in names:
                    self.error(f"{path}.name", f"关系名称重复：{name}")
                names.add(name)
            self.entity_ref(relation.get("from"), f"{path}.from")
            self.entity_ref(relation.get("to"), f"{path}.to")
            join = self.mapping(relation.get("join"), f"{path}.join")
            for key in ("from_table", "from_column", "to_table", "to_column"):
                self.text(join.get(key), f"{path}.join.{key}")
            if "via_table" in join:
                for key in ("via_table", "via_from_column", "via_to_column"):
                    self.text(join.get(key), f"{path}.join.{key}")
            elif "via_from_column" in join or "via_to_column" in join:
                self.error(f"{path}.join.via_table", "使用中间表连接列时必须声明 via_table")
            if "cardinality" in relation and (
                not isinstance(relation["cardinality"], str) or relation["cardinality"] not in _CARDINALITIES
            ):
                self.error(f"{path}.cardinality", "必须是 one_to_one、one_to_many、many_to_one 或 many_to_many")
            if "fanout" in relation and type(relation["fanout"]) is not bool:
                self.error(f"{path}.fanout", "必须是布尔值")
            cardinality = relation.get("cardinality")
            if isinstance(cardinality, str) and cardinality in _CARDINALITIES and type(relation.get("fanout")) is bool:
                expected = cardinality in {"one_to_many", "many_to_many"}
                if relation["fanout"] != expected:
                    self.warn(f"{path}.fanout", f"fanout 与 {cardinality} 的通常含义不一致，请人工确认连接方向与重复计数风险")

    def dimension_definitions(self) -> None:
        for _, dimension, path in self.named("dimensions"):
            entity = self.entity_ref(dimension.get("entity"), f"{path}.entity")
            self.property_ref(entity, dimension.get("property"), f"{path}.property")
            if "type" in dimension and dimension["type"] not in tuple(_PROPERTY_TYPES | {"dimension", "bucket"}):
                self.error(f"{path}.type", "不支持的维度类型")
            if "buckets" in dimension or dimension.get("type") == "bucket":
                buckets = self.sequence(dimension.get("buckets"), f"{path}.buckets")
                if not buckets:
                    self.error(f"{path}.buckets", "分桶列表不能为空")
                labels = set()
                intervals = []
                for i, raw in enumerate(buckets):
                    bucket_path = f"{path}.buckets[{i}]"
                    bucket = self.mapping(raw, bucket_path)
                    label = bucket.get("label")
                    if self.text(label, f"{bucket_path}.label"):
                        if label in labels:
                            self.error(f"{bucket_path}.label", f"分桶标签重复：{label}")
                        labels.add(label)
                    for key in ("min", "max"):
                        if type(bucket.get(key)) not in (int, float):
                            self.error(f"{bucket_path}.{key}", "必须声明有限数值边界")
                    if all(type(bucket.get(k)) in (int, float) for k in ("min", "max")):
                        if bucket["min"] > bucket["max"]:
                            self.error(bucket_path, "分桶下界不能大于上界")
                        else:
                            intervals.append((bucket["min"], bucket["max"], i))
                # Check the union in numeric order, retaining the declared order.
                # Inclusive endpoints and fractional gaps are advisory, never rewritten.
                covered = None
                for lower, upper, i in sorted(intervals):
                    if covered is not None:
                        if lower <= covered:
                            self.warn(f"{path}.buckets[{i}]", "分桶区间存在重叠或共用边界，请确认边界包含规则")
                        else:
                            self.warn(f"{path}.buckets[{i}]", f"分桶区间在 {covered} 与 {lower} 之间存在间隙，小数值可能未被覆盖")
                    covered = upper if covered is None else max(covered, upper)

    def metric_definitions(self) -> None:
        for _, metric, path in self.named("metrics"):
            if "entity" in metric:
                self.entity_ref(metric["entity"], f"{path}.entity")
            # Accept formula as an input alias, retaining it alongside expression.
            if "expression" not in metric and "formula" in metric:
                metric["expression"] = metric["formula"]
            self.text(metric.get("expression"), f"{path}.expression")
            if "formula" in metric:
                self.text(metric["formula"], f"{path}.formula")
                if metric["formula"] != metric.get("expression"):
                    self.error(f"{path}.formula", "formula 与 expression 不一致")
            if "filters" in metric:
                self.strings(metric["filters"], f"{path}.filters")
            for field in ("grain", "time_window", "deduplication", "null_policy"):
                if field in metric:
                    self.text(metric[field], f"{path}.{field}")
            if "dimensions" in metric:
                dimensions = self.strings(metric["dimensions"], f"{path}.dimensions")
                for i, name in enumerate(dimensions):
                    if isinstance(name, str) and name not in self.dimensions:
                        self.error(f"{path}.dimensions[{i}]", f"引用了未声明的维度：{name}")
            # Expressions/filters may use flattened view columns, so do not infer
            # their identifiers from the entity's declared properties.

    def synonym_definitions(self) -> None:
        for _, synonym, path in self.named("synonyms"):
            target = synonym.get("target")
            if self.text(target, f"{path}.target"):
                if target.startswith("Metric."):
                    if target[7:] not in self.metrics:
                        self.error(f"{path}.target", f"引用了未声明的指标：{target[7:]}")
                elif "." in target:
                    entity_name, prop = target.split(".", 1)
                    entity = self.entity_ref(entity_name, f"{path}.target")
                    self.property_ref(entity, prop, f"{path}.target")
                else:
                    self.entity_ref(target, f"{path}.target")
            if "default" in synonym and type(synonym["default"]) is not bool:
                self.error(f"{path}.default", "必须是布尔值")
            if "scope" in synonym:
                self.text(synonym["scope"], f"{path}.scope")

    def intent_default(self, value: Any, path: str) -> None:
        # _json_copy already ensures finite JSON scalars. Lists may contain scalars
        # or other lists, but structured objects are not runtime slot values.
        if isinstance(value, dict):
            self.error(path, "槽位默认值必须是 JSON 标量或列表，不能是映射对象")
        elif isinstance(value, list):
            for i, item in enumerate(value):
                self.intent_default(item, _field(path, i))

    def surfaces_and_intents(self) -> None:
        surfaces = self.sections["query_surface"]
        if surfaces:
            self.warn("$.query_surface", "查询表面仅作为声明保存，物理映射及授权过滤尚未验证或绑定")
        for _, surface, path in self.named("query_surface"):
            sources = set()
            for key in ("source_view", "source_table", "table", "view"):
                if key in surface and self.text(surface[key], f"{path}.{key}"):
                    sources.add(surface[key])
            if len(sources) > 1:
                self.warn(path, "物理来源别名声明不一致，请确认 source_view、source_table、table 与 view 指向的来源")
            if "columns" in surface:
                self.strings(surface["columns"], f"{path}.columns", unique=True)
            if not sources:
                self.warn(path, "未声明物理来源，需要由应用层提供实际查询映射")
        for _, intent, path in self.named("intents"):
            if "examples" in intent:
                self.strings(intent["examples"], f"{path}.examples")
            slots = {}
            for key in ("required_slots", "optional_slots"):
                values = self.strings(intent[key], f"{path}.{key}", unique=True, identifiers=True) if key in intent else []
                slots[key] = {value for value in values if isinstance(value, str)}
            if slots["required_slots"] & slots["optional_slots"]:
                self.error(f"{path}.optional_slots", "槽位不能同时声明为必填和可选")
            if "defaults" in intent:
                known_slots = slots["required_slots"] | slots["optional_slots"] | _RUNTIME_INTENT_SLOTS
                for slot, value in self.mapping(intent["defaults"], f"{path}.defaults").items():
                    default_path = _field(f"{path}.defaults", slot)
                    self.name(slot, default_path)
                    if slot not in known_slots:
                        self.error(default_path, "默认值槽位必须在本意图的 required_slots、optional_slots 或标准运行时槽位中声明")
                    self.intent_default(value, default_path)
            if "clarification" in intent:
                for i, raw in enumerate(self.sequence(intent["clarification"], f"{path}.clarification")):
                    item_path = f"{path}.clarification[{i}]"
                    clarification = self.mapping(raw, item_path)
                    for key in ("when", "question"):
                        self.text(clarification.get(key), f"{item_path}.{key}")

    def security(self) -> None:
        if "security" not in self.data:
            return
        security = self.mapping(self.data["security"], "$.security")
        self.warn("$.security", "安全声明仅作为知识保存，不会执行授权、行过滤或 fail_closed；访问控制必须由应用层实施")
        if "enforcement" in security:
            self.text(security["enforcement"], "$.security.enforcement")
        if "fail_closed" in security and type(security["fail_closed"]) is not bool:
            self.error("$.security.fail_closed", "必须是布尔值")
        if "notes" in security:
            self.strings(security["notes"], "$.security.notes")
        roles = self.mapping(security["roles"], "$.security.roles") if "roles" in security else {}
        if "default_role" in security:
            default_role = security["default_role"]
            if self.name(default_role, "$.security.default_role") and default_role not in roles:
                self.error("$.security.default_role", "默认角色未在 roles 中声明")
        for name, raw in roles.items():
            path = _field("$.security.roles", name)
            self.name(name, path)
            role = self.mapping(raw, path)
            self.metadata(role, path)
            if "required_context" in role:
                self.strings(role["required_context"], f"{path}.required_context", unique=True, identifiers=True)
            if "allowed_tables" in role:
                tables = self.strings(role["allowed_tables"], f"{path}.allowed_tables", unique=True)
                if "query_surface" in self.data:
                    for i, table in enumerate(tables):
                        if isinstance(table, str) and table not in self.sections["query_surface"]:
                            self.error(f"{path}.allowed_tables[{i}]", f"引用了未声明的查询表面：{table}")
            if "row_scope" in role:
                self.text(role["row_scope"], f"{path}.row_scope")

    def quality_rules(self) -> None:
        names = set()
        for i, raw in enumerate(self.sections["quality_rules"]):
            path = f"$.quality_rules[{i}]"
            rule = self.mapping(raw, path)
            self.metadata(rule, path)
            name = rule.get("name")
            if self.name(name, f"{path}.name"):
                if name in names:
                    self.error(f"{path}.name", f"质量规则名称重复：{name}")
                names.add(name)
            self.text(rule.get("expression"), f"{path}.expression")

    def ambiguity_warnings(self) -> None:
        # This is a declaration consistency heuristic, never a SQL evaluator.
        for i, rule in enumerate(self.sections["quality_rules"]):
            expression = rule["expression"].strip()
            description = rule.get("description", "").casefold()
            targets = {
                name for name, entity in self.entities.items()
                if expression in (name, entity.get("source", {}).get("table"), entity.get("source", {}).get("view"))
            }
            if targets and description:
                for name, metric in self.metrics.items():
                    terms = [name, metric.get("label", ""), *metric.get("synonyms", []), *metric.get("aliases", [])]
                    mentioned = any(len(term) >= 2 and term.casefold() in description for term in terms)
                    if mentioned and metric.get("entity") and metric["entity"] not in targets:
                        filters = "；".join(metric.get("filters", []))
                        detail = f"，过滤条件为 {filters}" if filters else ""
                        self.warn(_field("$.metrics", name), (
                            f"质量规则 {rule['name']} 指向数据源 {expression}，但该指标使用实体 "
                            f"{metric['entity']}{detail}；默认统计口径需要人工确认"
                        ))
            exclusion = re.fullmatch(r"(?:exclude|omit|without)[_-](.+)", rule["name"], re.IGNORECASE)
            if not exclusion:
                continue
            excluded = exclusion.group(1).casefold()
            candidates = []
            for entity_name, entity in self.entities.items():
                for prop_name, prop in entity.get("properties", {}).items():
                    values = prop.get("value_map", {})
                    if any(value.casefold() == excluded for value in values):
                        candidates.append((entity_name, prop_name))
            if candidates:
                # Require evidence of both the declared enum field and excluded
                # value. A warning asks for review; it does not establish SQL semantics.
                has_field = any(re.search(rf"(?<!\w){re.escape(prop)}(?!\w)", expression, re.IGNORECASE) for _, prop in candidates)
                has_value = re.search(rf"(?<!\w){re.escape(excluded)}(?!\w)", expression, re.IGNORECASE)
                if not has_field or not has_value:
                    fields = "、".join(f"{entity}.{prop}" for entity, prop in candidates)
                    self.warn(f"$.quality_rules[{i}].expression", (
                        f"规则名声明排除枚举值 {exclusion.group(1)}，但表达式未明确关联 {fields} "
                        "及该枚举值，不能确认实现了所声明的排除条件"
                    ))


def validate_ontology(payload: dict) -> dict:
    """Validate a decoded ontology, returning a detached JSON-safe canonical copy.

    Optional sections may be omitted. Metrics without an entity are supported for
    standalone formula definitions; every supplied entity reference is checked.
    Unknown JSON-safe extension fields are retained. No data source is accessed.
    """
    if not isinstance(payload, dict):
        raise _fail("$", "本体根节点必须是映射对象")
    data = _json_copy(payload)
    validator = _Validator(data)
    domain = validator.domain()
    meaningful_entities = validator.entity_definitions()
    validator.relations()
    validator.dimension_definitions()
    validator.metric_definitions()
    validator.synonym_definitions()
    validator.surfaces_and_intents()
    validator.security()
    validator.quality_rules()
    if not meaningful_entities and not validator.metrics:
        validator.error("$", "至少需要一个有实际定义的实体或指标")
    if validator.errors:
        raise OntologyValidationError(validator.errors)
    validator.ambiguity_warnings()
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _MAX_BYTES:
        raise _fail("$", "规范化后的本体内容不能超过 256 KiB（UTF-8 JSON）")
    return {
        "format": "ontology",
        "ontology": data,
        "summary": {
            "domain_name": domain["name"],
            "version": data["version"],
            "entities": len(validator.entities),
            "relations": len(validator.sections["relations"]),
            "dimensions": len(validator.dimensions),
            "metrics": len(validator.metrics),
            "intents": len(validator.sections["intents"]),
            "query_surfaces": len(validator.sections["query_surface"]),
            "synonyms": len(validator.sections["synonyms"]),
            "quality_rules": len(validator.sections["quality_rules"]),
            "properties": sum(len(entity.get("properties", {})) for entity in validator.entities.values()),
            "concepts": sum(
                not (entity.get("source", {}).get("table") or entity.get("source", {}).get("view"))
                for entity in validator.entities.values()
            ),
            "security_declared": "security" in data,
        },
        "warnings": validator.warnings,
    }
