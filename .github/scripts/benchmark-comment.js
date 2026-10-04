const fs = require('fs');
const { findStickyComment, upsertStickyComment } = require('./sticky-comment.js');

const MARKER = '<!-- benchmark-size -->';
const NO_CHANGES_TEXT = 'No benchmark changes.';
const MAX_COMMENT_LENGTH = 60000;
const DETAILS_TAG = '<details>';
const TOTALS_SECTION_COUNT = 2;
const SUMMARY_POINTER = '\n\nFull table in the job summary.\n';

function fits(markdown) {
  return `${MARKER}\n${markdown}`.length <= MAX_COMMENT_LENGTH;
}

function buildCommentBody(markdown) {
  if (fits(markdown)) {
    return markdown;
  }
  const detailsStart = markdown.lastIndexOf(DETAILS_TAG);
  const summaryOnly = detailsStart === -1 ? markdown : markdown.slice(0, detailsStart);
  const withoutDetails = `${summaryOnly.trimEnd()}${SUMMARY_POINTER}`;
  if (fits(withoutDetails)) {
    return withoutDetails;
  }
  const totals = markdown.split('\n\n').slice(0, TOTALS_SECTION_COUNT).join('\n\n');
  return `${totals}${SUMMARY_POINTER}`;
}

async function report({ github, context, core, path }) {
  const markdown = fs.readFileSync(path, 'utf8');
  await core.summary.addRaw(markdown).write();

  try {
    const existing = await findStickyComment(github, context, MARKER);
    const updateOnly = context.payload.pull_request.draft === true || markdown.includes(NO_CHANGES_TEXT);
    if (existing || !updateOnly) {
      await upsertStickyComment(github, context, MARKER, buildCommentBody(markdown), existing);
    }
  } catch (error) {
    if (error.status !== 403) {
      throw error;
    }
    core.warning(`Could not post the benchmark comment: ${error.message}`);
  }
}

module.exports = { report };
