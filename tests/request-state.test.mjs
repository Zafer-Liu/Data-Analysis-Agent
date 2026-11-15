import assert from "node:assert/strict";
import test from "node:test";
import { createRequestOwner } from "../frontend/core/request-state.js";

test("latest read owns its key and an old finish cannot remove the replacement", () => {
  const owner = createRequestOwner();
  const old = owner.beginRead("list");
  const current = owner.beginRead("list");
  assert.equal(old.signal.aborted, true);
  assert.equal(old.isCurrent(), false);
  old.finish();
  assert.equal(current.isCurrent(), true);
  current.finish();
  assert.equal(current.isCurrent(), false);
});

test("unrelated reads coexist; explicit cancellation only aborts its own key", () => {
  const owner = createRequestOwner();
  const list = owner.beginRead("list");
  const detail = owner.beginRead("detail");
  assert.equal(owner.cancelRead("list"), true);
  assert.equal(owner.cancelRead("list"), false);
  assert.equal(list.isCurrent(), false);
  assert.equal(detail.isCurrent(), true);
});

test("observed session/domain changes invalidate old reads, captures and writes", () => {
  let scope = "session-a:knowledge";
  const owner = createRequestOwner({ getScope: () => scope });
  const capture = owner.capture();
  const read = owner.beginRead("list");
  const write = owner.beginWrite("save");
  scope = "session-b:knowledge";
  assert.equal(capture.isCurrent(), false);
  assert.equal(read.signal.aborted, true);
  assert.equal(write.isCurrent(), false);
  const other = owner.beginWrite("save");
  assert.ok(other);
  scope = "session-a:knowledge";
  assert.equal(owner.beginWrite("save"), null);
  write.finish(); other.finish();
  assert.ok(owner.beginWrite("save"));
});

test("reset invalidates an A-B-A generation but keeps its write lock until settlement", () => {
  const owner = createRequestOwner({ getScope: () => "same-session" });
  const writing = owner.beginWrite("confirm");
  const reading = owner.beginRead("list");
  owner.reset();
  assert.equal(writing.isCurrent(), false);
  assert.equal(reading.signal.aborted, true);
  assert.equal(owner.hasWrite("confirm"), true);
  assert.equal(owner.beginWrite("confirm"), null);
  writing.finish();
  const replacement = owner.beginWrite("confirm");
  writing.finish();
  assert.equal(owner.hasWrite("confirm"), true);
  assert.equal(replacement.isCurrent(), true);
  replacement.finish();
  assert.equal(owner.hasWrite("confirm"), false);
});
