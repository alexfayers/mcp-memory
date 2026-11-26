// Pure UI logic for the visualiser: a CommonJS module in node, the `VisLib` global in a browser.
(function (root) {
  const shouldRefetchGraph = (events, previousPassTs, currentPassTs) =>
    events.some(ev => ev.kind !== "read") || (previousPassTs !== undefined && previousPassTs !== currentPassTs);

  const lib = { shouldRefetchGraph };
  if (typeof module !== "undefined") module.exports = lib;
  else root.VisLib = lib;
})(this);
