// Pure UI logic for the visualiser: a CommonJS module in node, the `VisLib` global in a browser.
(function (root) {
  const shouldRefetchGraph = (events, previousPassTs, currentPassTs) =>
    events.some(ev => ev.kind !== "read") || (previousPassTs !== undefined && previousPassTs !== currentPassTs);

  const filterEntities = (entities, { project = "", types = new Set(), statuses = new Set() }) =>
    entities.filter(e =>
      (!project || e.project === project)
      && (!types.size || types.has(e.entity_type))
      && (statuses.size ? statuses.has(e.status) : e.status !== "archived"));

  const formatRoute = ({ project, name }) =>
    "#" + encodeURIComponent(project) + (name ? "/" + encodeURIComponent(name).replace(/%2F/g, "/") : "");

  const parseRoute = hash => {
    const body = hash.replace(/^#/, ""), slash = body.indexOf("/");
    try {
      return slash < 0
        ? { project: decodeURIComponent(body), name: "" }
        : { project: decodeURIComponent(body.slice(0, slash)), name: decodeURIComponent(body.slice(slash + 1)) };
    } catch (e) { return { project: "", name: "" }; }
  };

  const neighbourhood = (relations, project, name) => {
    const groups = new Map();
    for (const r of relations) {
      if (r.project !== project || (r.source !== name && r.target !== name)) continue;
      const direction = r.source === name ? "out" : "in", key = r.relation_type + "\u0000" + direction;
      if (!groups.has(key)) groups.set(key, { type: r.relation_type, direction, names: [] });
      groups.get(key).names.push(direction === "out" ? r.target : r.source);
    }
    return [...groups.values()]
      .map(g => ({ ...g, names: g.names.sort() }))
      .sort((a, b) => a.type.localeCompare(b.type) || a.direction.localeCompare(b.direction));
  };

  const OPEN_STATUSES = new Set(["in-progress", "blocked"]);
  const openWork = (entities, project = "") => {
    const groups = new Map();
    entities
      .filter(e => OPEN_STATUSES.has(e.status) && (!project || e.project === project))
      .sort((a, b) => (a.updated_at || "").localeCompare(b.updated_at || ""))
      .forEach(e => { if (!groups.has(e.project)) groups.set(e.project, []); groups.get(e.project).push(e); });
    return [...groups].map(([name, list]) => ({ project: name, entities: list })).sort((a, b) => a.project.localeCompare(b.project));
  };

  const moveSelection = (index, delta, length) => {
    if (!length) return -1;
    if (index < 0) return delta > 0 ? 0 : length - 1;
    return Math.min(length - 1, Math.max(0, index + delta));
  };

  const lib = { shouldRefetchGraph, filterEntities, formatRoute, parseRoute, neighbourhood, openWork, moveSelection };
  if (typeof module !== "undefined") module.exports = lib;
  else root.VisLib = lib;
})(this);
