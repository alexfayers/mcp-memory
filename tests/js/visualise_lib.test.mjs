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

const entity = (name, entity_type, status, project = "p") => ({ name, entity_type, status, project });
const names = list => list.map(e => e.name);
const graph = [
  entity("t1", "task", "in-progress"),
  entity("t2", "task", "archived"),
  entity("f1", "feature", null),
  entity("t3", "task", "resolved", "q"),
];

test("filterEntities hides archived entities when no status chip is on", () => {
  assert.deepEqual(names(lib.filterEntities(graph, {})), ["t1", "f1", "t3"]);
});

test("filterEntities shows archived entities only when the archived chip is on", () => {
  assert.deepEqual(names(lib.filterEntities(graph, { statuses: new Set(["archived"]) })), ["t2"]);
  assert.deepEqual(names(lib.filterEntities(graph, { statuses: new Set(["archived", "resolved"]) })), ["t2", "t3"]);
});

test("filterEntities narrows by project and type", () => {
  assert.deepEqual(names(lib.filterEntities(graph, { project: "p" })), ["t1", "f1"]);
  assert.deepEqual(names(lib.filterEntities(graph, { types: new Set(["task"]) })), ["t1", "t3"]);
  assert.deepEqual(names(lib.filterEntities(graph, { project: "q", types: new Set(["feature"]) })), []);
});

test("formatRoute and parseRoute round-trip a project and an entity name with slashes", () => {
  const hash = lib.formatRoute({ project: "my proj", name: "task/a b" });
  assert.equal(hash, "#my%20proj/task/a%20b");
  assert.deepEqual(lib.parseRoute(hash), { project: "my proj", name: "task/a b" });
});

test("parseRoute reads a project-only or empty hash", () => {
  assert.deepEqual(lib.parseRoute("#global"), { project: "global", name: "" });
  assert.deepEqual(lib.parseRoute(""), { project: "", name: "" });
  assert.equal(lib.formatRoute({ project: "global", name: "" }), "#global");
  assert.equal(lib.formatRoute({ project: "", name: "" }), "#");
});

test("parseRoute ignores a malformed escape", () => {
  assert.deepEqual(lib.parseRoute("#%E0%A4%A"), { project: "", name: "" });
});

test("neighbourhood groups an entity's direct relations by type and direction", () => {
  const relations = [
    { source: "task/a", target: "feature/f", relation_type: "implements", project: "p" },
    { source: "task/b", target: "feature/f", relation_type: "implements", project: "p" },
    { source: "feature/f", target: "project/p", relation_type: "part-of", project: "p" },
    { source: "task/a", target: "task/b", relation_type: "depends-on", project: "p" },
    { source: "feature/f", target: "project/q", relation_type: "part-of", project: "q" },
  ];
  assert.deepEqual(lib.neighbourhood(relations, "p", "feature/f"), [
    { type: "implements", direction: "in", names: ["task/a", "task/b"] },
    { type: "part-of", direction: "out", names: ["project/p"] },
  ]);
});

test("neighbourhood is empty for an entity with no relations", () => {
  assert.deepEqual(lib.neighbourhood([], "p", "x"), []);
});

test("openWork groups in-progress and blocked entities by project, oldest update first", () => {
  const at = (name, status, project, updated_at) => ({ ...entity(name, "task", status, project), updated_at });
  const entities = [
    at("new", "in-progress", "p", "2026-03-01 00:00:00"),
    at("old", "blocked", "p", "2026-01-01 00:00:00"),
    at("done", "resolved", "p", "2025-01-01 00:00:00"),
    at("other", "in-progress", "a", "2026-02-01 00:00:00"),
  ];
  const groups = lib.openWork(entities).map(g => ({ project: g.project, names: names(g.entities) }));
  assert.deepEqual(groups, [{ project: "a", names: ["other"] }, { project: "p", names: ["old", "new"] }]);
});

test("openWork keeps only the chosen project", () => {
  const entities = [entity("x", "task", "blocked", "p"), entity("y", "task", "blocked", "q")];
  assert.deepEqual(lib.openWork(entities, "q").map(g => g.project), ["q"]);
});

test("moveSelection enters the list from either end and stops at its edges", () => {
  assert.equal(lib.moveSelection(-1, 1, 3), 0);
  assert.equal(lib.moveSelection(-1, -1, 3), 2);
  assert.equal(lib.moveSelection(0, -1, 3), 0);
  assert.equal(lib.moveSelection(1, 1, 3), 2);
  assert.equal(lib.moveSelection(2, 1, 3), 2);
});

test("moveSelection selects nothing in an empty list", () => {
  assert.equal(lib.moveSelection(-1, 1, 0), -1);
});
