import test from "node:test";
import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";
import { Sha256, sha256Hex } from "../../pi/maps/web/sha256.js";

test("the incremental hash matches the platform for every short length", () => {
  for (let n = 0; n <= 200; n++) {
    const bytes = randomBytes(n);
    const want = createHash("sha256").update(bytes).digest("hex");
    assert.equal(sha256Hex(bytes), want, `length ${n}`);
  }
});

test("a large file hashed in uneven pieces still matches", () => {
  const bytes = randomBytes(3 * 1024 * 1024 + 13);
  const want = createHash("sha256").update(bytes).digest("hex");
  const hash = new Sha256();
  for (let at = 0; at < bytes.length; at += 65537) {
    hash.update(bytes.subarray(at, Math.min(bytes.length, at + 65537)));
  }
  assert.equal(hash.hex(), want);
});
