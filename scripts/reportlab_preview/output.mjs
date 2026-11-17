// Offline preview files only. Never change permissions on an existing folder
// and never overwrite a file from an earlier run.
import { constants } from "node:fs";
import { chmod, lstat, mkdir, mkdtemp, open } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, parse, resolve } from "node:path";

function failure(code) {
  const error = new Error(code);
  error.code = code;
  return error;
}

async function directoryChain(directory) {
  const absolute = resolve(directory);
  const root = parse(absolute).root;
  let current = root;
  for (const component of absolute.slice(root.length).split("/").filter(Boolean)) {
    current = join(current, component);
    const metadata = await lstat(current);
    if (metadata.isSymbolicLink() || !metadata.isDirectory()) throw failure("preview_output_unsafe_directory");
  }
  return lstat(absolute);
}

function requirePrivate(metadata) {
  if (typeof process.getuid !== "function" || metadata.uid !== process.getuid()
      || (metadata.mode & 0o7777) !== 0o700) throw failure("preview_output_requires_owned_0700_directory");
}

export async function prepareOutputDirectory(requested) {
  if (requested === undefined) {
    const parent = resolve(tmpdir());
    await directoryChain(parent);
    const output = await mkdtemp(join(parent, "waf-reportlab-preview-"));
    await chmod(output, 0o700); // This call created the directory exclusively.
    requirePrivate(await directoryChain(output));
    return output;
  }
  if (typeof requested !== "string" || !requested.trim()) throw failure("preview_output_path_required");
  const output = resolve(requested);
  await directoryChain(dirname(output)); // Missing parents are never created.
  try {
    requirePrivate(await directoryChain(output));
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
    await mkdir(output, { mode: 0o700 }); // No recursive creation or EEXIST reuse.
    await chmod(output, 0o700);
    requirePrivate(await directoryChain(output));
  }
  return output;
}

export async function writePreviewFile(directory, name, content) {
  if (typeof name !== "string" || !/^[A-Za-z0-9_.-]+$/.test(name) || [".", ".."].includes(name)) throw failure("preview_output_invalid_filename");
  requirePrivate(await directoryChain(directory));
  const handle = await open(join(directory, name), constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
  try {
    await handle.chmod(0o600);
    await handle.writeFile(content, "utf8");
  } finally {
    await handle.close();
  }
}
