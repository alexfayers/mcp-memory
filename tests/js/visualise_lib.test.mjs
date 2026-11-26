import assert from "node:assert/strict";
import test from "node:test";

import lib from "../../src/mcp_memory/templates/visualise-lib.js";

const event = kind => ({ kind, tool: "t", entities: [], project: "p" });

test("shouldRefetchGraph ignores read-only activity", () => {
  assert.equal(lib.shouldRefetchGraph([event("read"), event("read")], 5, 5), false);
});

test("shouldRefetchGraph reloads on any write event", () => {
  for (const kind of ["create", "update", "delete"]) {
    assert.equal(lib.shouldRefetchGraph([event("read"), event(kind)], 5, 5), true);
  }
});

test("shouldRefetchGraph reloads when the dream last pass changes", () => {
  assert.equal(lib.shouldRefetchGraph([], 5, 6), true);
  assert.equal(lib.shouldRefetchGraph([], null, 6), true);
});

test("shouldRefetchGraph waits for the first dream status before comparing passes", () => {
  assert.equal(lib.shouldRefetchGraph([], undefined, 6), false);
});
