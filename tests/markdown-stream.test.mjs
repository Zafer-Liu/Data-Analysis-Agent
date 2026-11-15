// Contract tests for streaming Markdown split (frontend/features/markdown-stream.js).
//
// splitStableMarkdown separates fully-formed Markdown (safe to render) from a
// trailing incomplete block (must wait for more chunks). The function is
// intentionally conservative: a plain paragraph line is treated as an open
// block (a code fence or list could follow), so it stays in tail until a
// structural terminator (heading/HR with trailing newline, blank line closing
// a block) is seen. Only blocks that are structurally complete on their own
// are promoted to stable.
//
// These tests pin that contract so a refactor doesn't accidentally start
// promoting open paragraphs to stable (would render broken mid-stream) or
// demote complete headings (would cause flicker).
import { test } from "node:test";
import assert from "node:assert/strict";
import { splitStableMarkdown } from "../frontend/features/markdown-stream.js";

test("empty / null input yields empty stable and tail", () => {
  assert.deepEqual(splitStableMarkdown(""), { stable: "", tail: "" });
  assert.deepEqual(splitStableMarkdown(null), { stable: "", tail: "" });
  assert.deepEqual(splitStableMarkdown(undefined), { stable: "", tail: "" });
});

test("complete heading WITH trailing newline is stable", () => {
  const r = splitStableMarkdown("# Title\n");
  assert.equal(r.stable, "# Title\n");
  assert.equal(r.tail, "");
});

test("heading WITHOUT trailing newline stays in tail (could be continuation)", () => {
  const r = splitStableMarkdown("# Title");
  assert.equal(r.stable, "");
  assert.equal(r.tail, "# Title");
});

test("horizontal rule WITH trailing newline is stable", () => {
  const r = splitStableMarkdown("---\n");
  assert.equal(r.stable, "---\n");
  assert.equal(r.tail, "");
});

test("horizontal rule WITHOUT trailing newline stays in tail", () => {
  const r = splitStableMarkdown("---");
  assert.equal(r.stable, "");
  assert.equal(r.tail, "---");
});

test("unclosed code fence: fence + contents stay in tail", () => {
  const r = splitStableMarkdown("intro\n\n```python\nimport os\n");
  // The opening fence and its body must NOT be in stable (would render broken)
  assert.ok(!r.stable.includes("```python"));
  assert.ok(r.tail.includes("```python"));
  assert.ok(r.tail.includes("import os"));
});

test("closed code fence with trailing blank line is stable", () => {
  const src = "```python\nimport os\n```\n\n";
  const r = splitStableMarkdown(src);
  assert.equal(r.tail, "");
  assert.ok(r.stable.includes("```python"));
  assert.ok(r.stable.includes("import os"));
});

test("plain paragraph is conservative: stays in tail (list/fence may follow)", () => {
  const r = splitStableMarkdown("Hello world\n");
  assert.equal(r.stable, "");
  assert.equal(r.tail, "Hello world\n");
});

test("plain paragraph followed by heading: heading terminates, paragraph promoted", () => {
  // The heading with newline is a structural terminator, so everything up to
  // and including it becomes stable.
  const r = splitStableMarkdown("Hello world\n# Section\n");
  assert.ok(r.stable.includes("Hello world"));
  assert.ok(r.stable.includes("# Section"));
  assert.equal(r.tail, "");
});

test("open list: items stay in tail (no structural terminator yet)", () => {
  const r = splitStableMarkdown("- a\n- b\n");
  assert.equal(r.stable, "");
  assert.ok(r.tail.includes("- a"));
  assert.ok(r.tail.includes("- b"));
});
