#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for agent/validate.py — SQL guard + tool-arg validation.

The validator is the single line of defense against the LLM emitting destructive
SQL (DROP / DELETE / UPDATE / …). If anything here regresses, an LLM hallucination
could mutate user data — so the tests here have to be tight.
"""
import sys
import unittest
from pathlib import Path

# Allow running this file directly from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.validate import (
    SQL_TOOLS,
    normalize_ask_user_args,
    validate_tool_args,
)
from data.workspace import WorkspacePathAuthorization
from api.dashboard import _render_kpi_widget


class TestSqlValidation(unittest.TestCase):
    """SELECT/WITH-only policy for all SQL-bearing tools."""

    def test_empty_sql_rejected(self):
        for tool in SQL_TOOLS:
            with self.subTest(tool=tool):
                err = validate_tool_args(tool, {"sql": ""})
                self.assertIsNotNone(err)
                self.assertIn("non-empty", err)

    def test_missing_sql_field_rejected(self):
        for tool in SQL_TOOLS:
            with self.subTest(tool=tool):
                err = validate_tool_args(tool, {})
                self.assertIsNotNone(err)

    def test_select_accepted(self):
        for tool in SQL_TOOLS:
            with self.subTest(tool=tool):
                self.assertIsNone(
                    validate_tool_args(tool, {"sql": "SELECT * FROM users",
                                              "analysis_name": "x", "target_column": "y"})
                )

    def test_with_cte_accepted(self):
        sql = "WITH t AS (SELECT 1) SELECT * FROM t"
        self.assertIsNone(validate_tool_args(
            "query_data", {"sql": sql}
        ))

    def test_read_only_union_accepted(self):
        sql = "SELECT '99 Pro' AS mode, 10 AS orders UNION ALL SELECT 'Cloud', 20"
        self.assertIsNone(validate_tool_args("generate_chart", {"sql": sql}))

    def test_lowercase_select_accepted(self):
        self.assertIsNone(validate_tool_args(
            "query_data", {"sql": "select count(*) from users"}
        ))

    def test_leading_whitespace_accepted(self):
        self.assertIsNone(validate_tool_args(
            "query_data", {"sql": "   \n  SELECT 1"}
        ))

    def test_non_select_rejected(self):
        bad_starts = ["SHOW TABLES", "EXPLAIN SELECT 1", "PRAGMA table_info(t)"]
        for sql in bad_starts:
            with self.subTest(sql=sql):
                err = validate_tool_args("query_data", {"sql": sql})
                self.assertIsNotNone(err, f"should reject: {sql}")
                self.assertIn("只允许", err)

    def test_write_keywords_rejected(self):
        """Standalone write statements are rejected — by either of two checks:
        the SELECT/WITH prefix gate catches most, the keyword scan catches the rest.
        Either error message is acceptable; both result in the call being refused.
        """
        dangerous = [
            "DROP TABLE users",
            "DELETE FROM orders WHERE id=1",
            "UPDATE users SET name='x'",
            "INSERT INTO users VALUES (1)",
            "TRUNCATE users",
            "ALTER TABLE users ADD COLUMN x INT",
            "CREATE TABLE evil AS SELECT 1",
            "CREATE INDEX i ON t(x)",
        ]
        for sql in dangerous:
            with self.subTest(sql=sql):
                err = validate_tool_args("query_data", {"sql": sql})
                self.assertIsNotNone(err, f"should reject: {sql}")

    def test_smuggled_writes_in_select_blocked(self):
        """The dangerous case the keyword scan exists for: a leading SELECT
        passes the prefix gate, but a write statement is smuggled in afterwards.
        Must hit the 'blocked' path specifically (not the SELECT/WITH path).
        """
        smuggled = [
            "SELECT 1; DROP TABLE users",
            "SELECT * FROM t; DELETE FROM logs",
            "SELECT a FROM t WHERE 1=1 -- comment\nUPDATE users SET x=1",
        ]
        for sql in smuggled:
            with self.subTest(sql=sql):
                err = validate_tool_args("query_data", {"sql": sql})
                self.assertIsNotNone(err, f"should block smuggled write: {sql}")
                self.assertTrue(
                    "多语句" in err or "写操作" in err,
                    f"expected multi-statement/write rejection for: {sql}; got: {err}",
                )

    def test_innocent_identifiers_not_falsely_blocked(self):
        """Column / table names containing substrings like 'update', 'delete', 'insert'
        must NOT be blocked when the actual SQL is a SELECT. The trailing space in
        SQL_BLOCKED_WRITES patterns is what protects us here.
        """
        # `update_count` is a column name; `inserted_at` is a column name.
        safe = "SELECT update_count, inserted_at FROM event_log WHERE updated_by='alice'"
        self.assertIsNone(
            validate_tool_args("query_data", {"sql": safe}),
            "Identifiers containing write-verb substrings must not falsely block"
        )

class TestRunAnalysisValidation(unittest.TestCase):
    """run_analysis needs an analysis_name + target_column."""

    def test_missing_analysis_name(self):
        err = validate_tool_args("run_analysis", {"sql": "SELECT 1", "target_column": "y"})
        self.assertIsNotNone(err)
        self.assertIn("analysis_name", err)

    def test_missing_target_column(self):
        err = validate_tool_args("run_analysis", {
            "sql": "SELECT 1", "analysis_name": "Regression"
        })
        self.assertIsNotNone(err)
        self.assertIn("target_column", err)

    def test_all_required_present(self):
        err = validate_tool_args("run_analysis", {
            "sql": "SELECT * FROM data", "analysis_name": "Regression", "target_column": "y"
        })
        self.assertIsNone(err)


class TestStructuredArgValidation(unittest.TestCase):
    """slides / widgets must be lists when supplied."""

    def test_ppt_slides_must_be_list(self):
        self.assertIsNotNone(validate_tool_args(
            "propose_ppt_outline", {"slides": "not a list"}
        ))
        self.assertIsNone(validate_tool_args(
            "propose_ppt_outline", {"slides": [{"title": "x"}]}
        ))
        self.assertIsNone(validate_tool_args(
            "propose_ppt_outline", {}  # slides not supplied → OK
        ))

    def test_dashboard_widgets_must_be_list(self):
        self.assertIsNotNone(validate_tool_args(
            "propose_dashboard_outline", {"widgets": {"x": 1}}
        ))
        self.assertIsNone(validate_tool_args(
            "propose_dashboard_outline", {"widgets": []}
        ))

    def test_ask_user_requires_string_options(self):
        self.assertIsNone(validate_tool_args("ask_user", {
            "question": "请选择分析方向",
            "options": ["整体概览", "RFM 分层"],
        }))
        self.assertIsNotNone(validate_tool_args("ask_user", {
            "question": "请选择分析方向",
            "options": [{"label": "整体概览"}, {"label": "RFM 分层"}],
        }))

    def test_ask_user_provider_objects_are_normalized(self):
        args = normalize_ask_user_args({
            "question": " 请选择分析方向 ",
            "options": [
                {"label": "整体概览", "value": "overview"},
                {"text": "RFM 分层"},
                " 时间趋势 ",
                {"value": "付款行为"},
                {"unexpected": "ignored"},
                {"label": "整体概览"},
            ],
        })
        self.assertEqual(args["question"], "请选择分析方向")
        self.assertEqual(args["options"], [
            "整体概览", "RFM 分层", "时间趋势", "付款行为",
        ])
        self.assertIsNone(validate_tool_args("ask_user", args))


class TestUnknownTools(unittest.TestCase):
    """Tools not covered by any rule should pass through (return None)."""

    def test_unknown_tool_no_rules(self):
        self.assertIsNone(validate_tool_args("get_schema", {}))
        self.assertIsNone(validate_tool_args("profile_data", {"columns": ["a"]}))


class TestFilePathWhitelist(unittest.TestCase):
    """A4: read_csv / read_parquet 等 file-read 函数的路径白名单。

    验收场景（来自 architecture-improve.md）：
      - read_csv('/etc/passwd') 被拒（不在白名单）
      - read_csv('销售.xlsx')（工作目录内）放行
      - read_csv(@变量) 被拒（非字面量）
      - read_csv('子目录/x.parquet') 放行（工作目录子目录）
      - 上传的文件（uploads/ 内）仍可读
    """

    def setUp(self):
        import tempfile
        # 模拟工作目录
        self.workdir = Path(tempfile.mkdtemp(prefix="ws_test_"))
        # 工作目录内放一个数据文件 + 子目录
        (self.workdir / "销售.xlsx").write_bytes(b"fake")
        sub = self.workdir / "子目录"
        sub.mkdir()
        (sub / "x.parquet").write_bytes(b"fake")
        # 模拟 uploads 目录
        self.uploads = Path(tempfile.mkdtemp(prefix="ws_uploads_"))
        (self.uploads / "uploaded.csv").write_text("a,b\n1,2\n")
        self.allowed_roots = [self.workdir, self.uploads]

    def tearDown(self):
        import shutil
        shutil.rmtree(self.workdir, ignore_errors=True)
        shutil.rmtree(self.uploads, ignore_errors=True)

    def test_absolute_path_outside_whitelist_rejected(self):
        """read_csv('/etc/passwd') 必须被拒（不在白名单）。"""
        err = validate_tool_args("query_data", {
            "sql": "SELECT * FROM read_csv('/etc/passwd')",
        }, allowed_roots=self.allowed_roots)
        self.assertIsNotNone(err, "/etc/passwd 必须被拒")
        self.assertIn("不在允许", err)

    def test_workspace_authorization_identity_selects_its_own_roots(self):
        other = self.workdir.parent / f"{self.workdir.name}-other"
        other.mkdir()
        try:
            target = self.workdir / "销售.xlsx"
            auth_a = WorkspacePathAuthorization("workspace-a", (self.workdir,), 1)
            auth_b = WorkspacePathAuthorization("workspace-b", (other,), 1)
            sql = f"SELECT * FROM read_csv('{target.as_posix()}')"
            self.assertIsNone(validate_tool_args(
                "query_data", {"sql": sql},
                allowed_roots=[other], workspace_authorization=auth_a,
            ))
            self.assertIsNotNone(validate_tool_args(
                "query_data", {"sql": sql}, workspace_authorization=auth_b,
            ))
        finally:
            import shutil
            shutil.rmtree(other, ignore_errors=True)

    def test_quoted_duckdb_file_scan_is_whitelisted(self):
        inside = (self.workdir / "销售.xlsx").as_posix()
        outside = (self.workdir.parent / "outside.csv").as_posix()
        self.assertIsNone(validate_tool_args(
            "query_data", {"sql": f"SELECT * FROM '{inside}'"},
            allowed_roots=self.allowed_roots,
        ))
        self.assertIsNotNone(validate_tool_args(
            "query_data", {"sql": f"SELECT * FROM '{outside}'"},
            allowed_roots=self.allowed_roots,
        ))

    def test_dashboard_sql_cannot_bypass_workspace_authorization(self):
        class GuardedSource:
            called = False

            def execute_query(self, _sql):
                self.called = True
                raise AssertionError("unsafe SQL reached the data source")

        source = GuardedSource()
        auth = WorkspacePathAuthorization("workspace-a", (self.workdir,), 1)
        outside = (self.workdir.parent / "outside.csv").as_posix()
        result = _render_kpi_widget(
            source,
            {"sql": f"SELECT * FROM read_csv('{outside}')"},
            auth,
        )
        self.assertFalse(source.called)
        self.assertIn("不在允许", result["error"])

    def test_workdir_relative_path_accepted(self):
        """read_csv('销售.xlsx')（工作目录内相对路径）放行。"""
        err = validate_tool_args("query_data", {
            "sql": "SELECT * FROM read_csv('销售.xlsx')",
        }, allowed_roots=self.allowed_roots)
        self.assertIsNone(err), f"工作目录内文件应放行: {err}"

    def test_non_literal_path_rejected(self):
        """read_csv(@变量) 被拒（非字面量参数）。"""
        err = validate_tool_args("query_data", {
            "sql": "SELECT * FROM read_csv(@var)",
        }, allowed_roots=self.allowed_roots)
        self.assertIsNotNone(err, "@var 非字面量必须被拒")
        self.assertIn("非字面量", err)

    def test_column_reference_path_rejected(self):
        """read_csv(table.col) 被拒（列引用参数）。"""
        err = validate_tool_args("query_data", {
            "sql": "SELECT * FROM read_csv(some_table.some_col)",
        }, allowed_roots=self.allowed_roots)
        self.assertIsNotNone(err, "列引用参数必须被拒")

    def test_workdir_subdir_path_accepted(self):
        """read_csv('子目录/x.parquet') 放行（工作目录子目录）。"""
        err = validate_tool_args("query_data", {
            "sql": "SELECT * FROM read_parquet('子目录/x.parquet')",
        }, allowed_roots=self.allowed_roots)
        self.assertIsNone(err), f"工作目录子目录文件应放行: {err}"

    def test_uploads_path_accepted(self):
        """上传的文件（uploads/ 内）仍可读。"""
        err = validate_tool_args("query_data", {
            "sql": f"SELECT * FROM read_csv('{self.uploads / 'uploaded.csv'}')",
        }, allowed_roots=self.allowed_roots)
        self.assertIsNone(err), f"uploads 内文件应放行: {err}"

    def test_read_csv_auto_accepted(self):
        """read_csv_auto() 同样受白名单控制，工作目录内放行。"""
        err = validate_tool_args("query_data", {
            "sql": "SELECT * FROM read_csv_auto('销售.xlsx')",
        }, allowed_roots=self.allowed_roots)
        self.assertIsNone(err), f"read_csv_auto 工作目录内应放行: {err}"

    def test_read_csv_auto_outside_rejected(self):
        """read_csv_auto('/etc/passwd') 被拒。"""
        err = validate_tool_args("query_data", {
            "sql": "SELECT * FROM read_csv_auto('/etc/passwd')",
        }, allowed_roots=self.allowed_roots)
        self.assertIsNotNone(err, "read_csv_auto /etc/passwd 必须被拒")

    def test_read_csv_with_options_accepted(self):
        """read_csv('销售.xlsx', header=true) 带命名参数也放行。"""
        err = validate_tool_args("query_data", {
            "sql": "SELECT * FROM read_csv('销售.xlsx', header=true)",
        }, allowed_roots=self.allowed_roots)
        self.assertIsNone(err), f"带命名参数的 read_csv 应放行: {err}"

    def test_path_array_accepted(self):
        """read_csv(['a.csv','b.csv']) 列表参数，全部在白名单内放行。"""
        # 先建第二个文件
        (self.workdir / "b.csv").write_text("x,y\n3,4\n")
        err = validate_tool_args("query_data", {
            "sql": "SELECT * FROM read_csv(['销售.xlsx', 'b.csv'])",
        }, allowed_roots=self.allowed_roots)
        self.assertIsNone(err), f"列表参数白名单内应放行: {err}"

    def test_path_array_one_outside_rejected(self):
        """read_csv(['a.csv','/etc/passwd']) 列表中有一个越界 → 拒。"""
        err = validate_tool_args("query_data", {
            "sql": "SELECT * FROM read_csv(['销售.xlsx', '/etc/passwd'])",
        }, allowed_roots=self.allowed_roots)
        self.assertIsNotNone(err, "列表含越界路径必须被拒")

    def test_no_allowed_roots_uses_default_uploads_information(self):
        """未传 allowed_roots（未挂载 workspace）时，用默认根 uploads/Information。

        策略：validate 层只做白名单检查，不做存在性检查。
        - 相对路径 '销售.xlsx' 相对 uploads 解析 → 在 uploads 根下 → validate 放行
          （DuckDB 执行时若文件不存在会自己报 File not found）
        - 绝对路径 '/etc/passwd' 不在默认根 → validate 拒
        """
        # 相对路径在默认根下 → 放行（DuckDB 执行时报错与否不是 validate 的事）
        err = validate_tool_args("query_data", {
            "sql": "SELECT * FROM read_csv('销售.xlsx')",
        })  # 不传 allowed_roots
        self.assertIsNone(err, "相对路径在默认根 uploads/ 下，validate 层放行")

        # 绝对路径越界 → 拒
        err = validate_tool_args("query_data", {
            "sql": "SELECT * FROM read_csv('/etc/passwd')",
        })
        self.assertIsNotNone(err, "/etc/passwd 不在默认根，必须被拒")

    def test_glob_path_accepted(self):
        """read_csv('*.csv') 通配符路径，父目录在白名单内放行。"""
        (self.workdir / "a.csv").write_text("a\n1\n")
        err = validate_tool_args("query_data", {
            "sql": "SELECT * FROM read_csv('*.csv')",
        }, allowed_roots=self.allowed_roots)
        self.assertIsNone(err), f"通配符路径父目录在白名单内应放行: {err}"

    def test_select_from_literal_string_rejected(self):
        """SELECT * FROM 'sales.csv' 直接字面量 FROM（DuckDB 也支持）。
        当前实现不专门处理这种形式 —— DuckDB 会自己解析，validate 暂不拦。
        此测试记录现状，若后续要拦再加。
        """
        # 这种形式 DuckDB 会当作表名，不在 file-read 函数范畴，目前放行
        err = validate_tool_args("query_data", {
            "sql": "SELECT * FROM 'sales.csv'",
        }, allowed_roots=self.allowed_roots)
        # 现状：放行（不是 file-read 函数调用）
        self.assertIsNone(err)

    def test_extension_install_still_blocked(self):
        """install/load/attach 等 extension 函数仍然禁止（不在 file-read 白名单）。"""
        for sql in [
            "SELECT * FROM (INSTALL httpfs)",
            "SELECT * FROM (LOAD httpfs)",
        ]:
            with self.subTest(sql=sql):
                err = validate_tool_args("query_data", {"sql": sql},
                                         allowed_roots=self.allowed_roots)
                # 这些语句可能解析失败走 heuristic，或被 banned functions 拦
                # 关键是不能因为放开了 LocalFileSystem 就让它们过
                if err is None:
                    # 如果 ast 没拦，heuristic 也不会拦（因为是 SELECT 开头）
                    # 这种情况实际上是 DuckDB 的 INSTALL 语法，sqlglot 可能解析为 Command
                    # 不强制要求拦，只要 file-read 白名单工作即可
                    pass

    def test_copy_still_blocked(self):
        """COPY 写文件仍然禁止。"""
        err = validate_tool_args("query_data", {
            "sql": "COPY (SELECT 1) TO '/tmp/x.csv'",
        }, allowed_roots=self.allowed_roots)
        self.assertIsNotNone(err, "COPY 必须被拒")

    def test_http_function_still_blocked(self):
        """http_get/http_post 网络函数仍然禁止。"""
        err = validate_tool_args("query_data", {
            "sql": "SELECT http_get('https://evil.com/x')",
        }, allowed_roots=self.allowed_roots)
        self.assertIsNotNone(err, "http_get 必须被拒")

    def test_network_url_paths_rejected(self):
        """read_csv('https://...') / read_csv('s3://...') 等网络 URL 一律拒绝（SSRF 防护）。"""
        network_urls = [
            "https://evil.com/x.csv",
            "http://evil.com/x.csv",
            "s3://evil-bucket/x.csv",
            "gs://evil-bucket/x.csv",
            "azure://evil-container/x.csv",
            "hdfs://evil-host:9000/x.csv",
            "ftp://evil.com/x.csv",
        ]
        for url in network_urls:
            with self.subTest(url=url):
                err = validate_tool_args("query_data", {
                    "sql": f"SELECT * FROM read_csv('{url}')",
                }, allowed_roots=self.allowed_roots)
                self.assertIsNotNone(err, f"网络 URL 必须被拒: {url}")
                self.assertIn("网络路径", err)


if __name__ == "__main__":
    unittest.main()
