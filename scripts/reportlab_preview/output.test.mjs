import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { chmod, lstat, mkdir, mkdtemp, readFile, readdir, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { prepareOutputDirectory, writePreviewFile } from "./output.mjs";

const run = promisify(execFile);
const permissions = metadata => metadata.mode & 0o7777;

async function workspace(t) {
  const root = await mkdtemp(join(tmpdir(), "waf-preview-output-test-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  return root;
}

test("default output uses distinct current-user private directories", async t => {
  const first = await prepareOutputDirectory();
  t.after(() => rm(first, { recursive: true, force: true }));
  const second = await prepareOutputDirectory();
  t.after(() => rm(second, { recursive: true, force: true }));
  assert.notEqual(first, second);
  for (const directory of [first, second]) {
    const metadata = await lstat(directory);
    assert.equal(metadata.uid, process.getuid());
    assert.equal(permissions(metadata), 0o700);
    assert.equal(metadata.isSymbolicLink(), false);
  }
});

test("existing mktemp directories are accepted and a new leaf is private", async t => {
  const root = await workspace(t);
  assert.equal(await prepareOutputDirectory(root), root);
  const output = join(root, "new-output");
  assert.equal(await prepareOutputDirectory(output), output);
  assert.equal(permissions(await lstat(output)), 0o700);
  assert.equal(permissions(await lstat(root)), 0o700);
});

test("missing parents and invalid paths are refused without recursive creation", async t => {
  const root = await workspace(t);
  await assert.rejects(prepareOutputDirectory(join(root, "missing", "output")), { code: "ENOENT" });
  assert.deepEqual(await readdir(root), []);
  for (const path of ["", " ", null, 1]) await assert.rejects(prepareOutputDirectory(path), { code: "preview_output_path_required" });
});

test("existing public directories are refused without permission changes", async t => {
  const root = await workspace(t);
  const output = join(root, "public");
  await mkdir(output);
  await chmod(output, 0o755);
  await assert.rejects(prepareOutputDirectory(output), { code: "preview_output_requires_owned_0700_directory" });
  assert.equal(permissions(await lstat(output)), 0o755);
  assert.deepEqual(await readdir(output), []);
});

test("another owner's directory is not reused or chmodded", async t => {
  const root = await workspace(t);
  const owner = process.getuid();
  t.mock.method(process, "getuid", () => owner + 1);
  await assert.rejects(prepareOutputDirectory(root), { code: "preview_output_requires_owned_0700_directory" });
  assert.equal(permissions(await lstat(root)), 0o700);
  assert.equal((await lstat(root)).uid, owner);
});

test("symbolic links in the output or any parent are refused", async t => {
  const root = await workspace(t);
  const real = join(root, "real");
  await mkdir(real, { mode: 0o700 });
  const link = join(root, "linked");
  await symlink(real, link, "dir");
  for (const path of [link, join(link, "nested")]) {
    await assert.rejects(prepareOutputDirectory(path), { code: "preview_output_unsafe_directory" });
  }
  assert.deepEqual(await readdir(real), []);
});

test("new files are 0600 and existing regular files or links are never replaced", async t => {
  const root = await workspace(t);
  await writePreviewFile(root, "report.json", '{"synthetic":true}');
  assert.equal(permissions(await lstat(join(root, "report.json"))), 0o600);
  await assert.rejects(writePreviewFile(root, "report.json", "overwrite"), { code: "EEXIST" });
  assert.equal(await readFile(join(root, "report.json"), "utf8"), '{"synthetic":true}');
  const protectedFile = join(root, "preserve.txt");
  await writeFile(protectedFile, "preserve", { mode: 0o600 });
  await symlink(protectedFile, join(root, "linked.json"));
  await assert.rejects(writePreviewFile(root, "linked.json", "overwrite"));
  assert.equal(await readFile(protectedFile, "utf8"), "preserve");
  assert.equal((await lstat(join(root, "linked.json"))).isSymbolicLink(), true);
});

test("writes reject path traversal and a directory made public after selection", async t => {
  const root = await workspace(t);
  for (const name of ["../outside", "/absolute", ".", "..", "nested/file", "nested\\file"])
    await assert.rejects(writePreviewFile(root, name, "synthetic"), { code: "preview_output_invalid_filename" });
  await chmod(root, 0o755);
  await assert.rejects(writePreviewFile(root, "report.json", "synthetic"), { code: "preview_output_requires_owned_0700_directory" });
  assert.deepEqual(await readdir(root), []);
});

test("new directory and file permissions remain exact under a restrictive umask", async t => {
  const root = await workspace(t);
  const previous = process.umask(0o777);
  try {
    const output = await prepareOutputDirectory(join(root, "restricted"));
    await writePreviewFile(output, "report.md", "synthetic");
    assert.equal(permissions(await lstat(output)), 0o700);
    assert.equal(permissions(await lstat(join(output, "report.md"))), 0o600);
  } finally {
    process.umask(previous);
  }
});

test("prepare CLI accepts the documented mktemp path and reports its output", async t => {
  const output = await workspace(t);
  const script = fileURLToPath(new URL("./prepare.mjs", import.meta.url));
  const { stdout, stderr } = await run(process.execPath, [script, output]);
  assert.equal(stderr, "");
  assert.ok(stdout.includes(`Preview output: ${output}`));
  assert.match(stdout, /no LLM calls/);
  const files = await readdir(output);
  assert.equal(files.length, 9);
  for (const file of files) assert.equal(permissions(await lstat(join(output, file))), 0o600);
  assert.match(await readFile(join(output, "01-short.md"), "utf8"), /WAF 분석 보고서/);
  await assert.rejects(run(process.execPath, [script, output]));
  assert.equal((await readdir(output)).length, 9);
});
