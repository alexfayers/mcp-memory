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

  const lib = { shouldRefetchGraph, filterEntities, formatRoute, parseRoute };
  if (typeof module !== "undefined") module.exports = lib;
  else root.VisLib = lib;
})(this);
