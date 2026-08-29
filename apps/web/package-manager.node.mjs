import { readFileSync } from "node:fs";
import { URL } from "node:url";
import assert from "node:assert/strict";
import test from "node:test";

test("package manager uses an exact pnpm semver", () => {
  const packageJson = JSON.parse(
    readFileSync(new URL("./package.json", import.meta.url), "utf8"),
  );

  assert.match(packageJson.packageManager, /^pnpm@\d+\.\d+\.\d+$/);
  assert.equal(packageJson.packageManager, "pnpm@11.19.0");
});
