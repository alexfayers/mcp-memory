const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { report, reportForRun } = require('./benchmark-comment.js');

const MARKER = '<!-- benchmark-size -->';

function setup(markdown, { draft = false, comments = [], failWith } = {}) {
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'benchmark-')), 'benchmark.md');
  fs.writeFileSync(file, markdown);
  const calls = { create: [], update: [], summary: [], warnings: [] };
  const fail = async () => {
    if (failWith) throw failWith;
  };
  const github = {
    paginate: async () => comments,
    rest: {
      issues: {
        listComments: () => {},
        createComment: async (params) => {
          await fail();
          calls.create.push(params);
        },
        updateComment: async (params) => {
          await fail();
          calls.update.push(params);
        },
      },
    },
  };
  const core = {
    summary: {
      addRaw: (text) => {
        calls.summary.push(text);
        return core.summary;
      },
      write: async () => {},
    },
    warning: (message) => calls.warnings.push(message),
  };
  const context = {
    repo: { owner: 'o', repo: 'r' },
    payload: { pull_request: { number: 3, draft } },
  };
  return { args: { github, context, core, path: file }, calls };
}

test('creates a marked comment and writes the full report to the job summary', async () => {
  const { args, calls } = setup('| a | b |\n');
  await report(args);
  assert.deepEqual(calls.summary, ['| a | b |\n']);
  assert.equal(calls.create.length, 1);
  assert.ok(calls.create[0].body.startsWith(`${MARKER}\n`));
  assert.ok(!calls.create[0].body.slice(MARKER.length).includes(MARKER));
  assert.ok(calls.create[0].body.includes('| a | b |'));
});

test('updates the existing marked comment instead of creating one and leaves other comments alone', async () => {
  const { args, calls } = setup('changes\n', {
    comments: [
      { id: 1, body: '<!-- pr-template-check -->\nx' },
      { id: 9, body: `${MARKER}\nold` },
    ],
  });
  await report(args);
  assert.equal(calls.update.length, 1);
  assert.equal(calls.update[0].comment_id, 9);
  assert.equal(calls.create.length, 0);
});

test('creates a comment on a draft PR and updates an existing one', async () => {
  const draft = setup('changes\n', { draft: true });
  await report(draft.args);
  assert.equal(draft.calls.create.length, 1);

  const existing = setup('changes\n', { draft: true, comments: [{ id: 4, body: MARKER }] });
  await report(existing.args);
  assert.equal(existing.calls.update.length, 1);
});

test('creates a comment for a no-change report and updates an existing one', async () => {
  const none = setup('No benchmark changes.\n');
  await report(none.args);
  assert.equal(none.calls.create.length, 1);

  const existing = setup('No benchmark changes.\n', { comments: [{ id: 4, body: MARKER }] });
  await report(existing.args);
  assert.equal(existing.calls.update.length, 1);
});

test('drops the trailing details block from an oversize comment but keeps it in the summary', async () => {
  const markdown = `summary line\n<details>\n${'x'.repeat(60000)}\n</details>\n`;
  const { args, calls } = setup(markdown);
  await report(args);
  const { body } = calls.create[0];
  assert.ok(body.length <= 60000);
  assert.ok(!body.includes('<details>'));
  assert.ok(body.includes('summary line'));
  assert.ok(body.includes('Full table in the job summary.'));
  assert.equal(calls.summary[0], markdown);
});

test('falls back to the badges and headline when the content before the details block is still oversize', async () => {
  const top = '![output size](u) ![ranking](u)\n\n**Output size** 1 -> 2 bytes';
  const markdown = `${top}\n\n<details>\n${'x'.repeat(60000)}\n\n</details>\n\n<details>\n</details>\n`;
  const { args, calls } = setup(markdown);
  await report(args);
  const { body } = calls.create[0];
  assert.ok(body.length <= 60000);
  assert.ok(body.includes('**Output size** 1 -> 2 bytes'));
  assert.ok(!body.includes('xxx'));
  assert.ok(body.includes('Full table in the job summary.'));
});

test('warns instead of throwing on a 403 and rethrows other errors', async () => {
  const forbidden = setup('changes\n', { failWith: Object.assign(new Error('forbidden'), { status: 403 }) });
  await report(forbidden.args);
  assert.equal(forbidden.calls.warnings.length, 1);

  const broken = setup('changes\n', { failWith: Object.assign(new Error('boom'), { status: 500 }) });
  await assert.rejects(report(broken.args), /boom/);
});

test('counts the marker toward the comment size cap', async () => {
  const head = 'summary line\n<details>\n';
  const markdown = `${head}${'x'.repeat(60000 - head.length - 1)}\n`;
  assert.equal(markdown.length, 60000);
  const { args, calls } = setup(markdown);
  await report(args);
  assert.ok(calls.create[0].body.length <= 60000);
  assert.ok(calls.create[0].body.includes('Full table in the job summary.'));
});

function runSetup(prNumber, { prHeadSha = 'abc', runHeadSha = 'abc' } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'benchmark-artifact-'));
  fs.writeFileSync(path.join(dir, 'benchmark.md'), 'changes\n');
  fs.writeFileSync(path.join(dir, 'pr-number'), `${prNumber}\n`);
  const { args, calls } = setup('');
  args.github.rest.pulls = { get: async () => ({ data: { head: { sha: prHeadSha } } }) };
  const context = { repo: args.context.repo, payload: { workflow_run: { head_sha: runHeadSha } } };
  return { args: { github: args.github, context, core: args.core, dir }, calls };
}

test('comments on the PR named in the artifact when its head matches the run', async () => {
  const { args, calls } = runSetup(7);
  await reportForRun(args);
  assert.equal(calls.create[0].issue_number, 7);
  assert.ok(calls.create[0].body.includes('changes'));
});

test('skips the comment when the artifact PR number is not a number', async () => {
  const { args, calls } = runSetup('7; rm');
  await reportForRun(args);
  assert.equal(calls.create.length, 0);
  assert.equal(calls.warnings.length, 1);
});

test('skips the comment when the PR head does not match the run', async () => {
  const { args, calls } = runSetup(7, { prHeadSha: 'other' });
  await reportForRun(args);
  assert.equal(calls.create.length, 0);
  assert.equal(calls.warnings.length, 1);
});
