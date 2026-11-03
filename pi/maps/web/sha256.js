/* PROJECT ARK — SHA-256, computed as the bytes arrive.
 *
 * The browser can hash a whole buffer with WebCrypto, but a map pack must be
 * hashed while it is being downloaded: holding a 20 MB file twice to check it is
 * exactly the memory cost this feature is trying to avoid, and a 200 MB one would
 * not fit on a modest phone at all.  This is the standard algorithm, incremental,
 * with no dependency and no network.
 *
 * The tests compare it against Node's crypto for every length from 0 to 300 and
 * for a few megabytes of random data — a hash that is only nearly right is a
 * download that is only nearly verified.
 */

const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
  0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
  0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
  0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
  0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
  0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
  0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
  0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
  0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

function rotr(x, n) {
  return (x >>> n) | (x << (32 - n));
}

export class Sha256 {
  constructor() {
    this.h = new Uint32Array([
      0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
      0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    ]);
    this.block = new Uint8Array(64);
    this.blockLen = 0;
    this.length = 0;           // bytes seen, as a safe integer (< 2^53)
    this.w = new Int32Array(64);
    this.finished = false;
  }

  _compress(bytes, at) {
    const w = this.w;
    for (let i = 0; i < 16; i++) {
      const j = at + i * 4;
      w[i] = (bytes[j] << 24) | (bytes[j + 1] << 16) | (bytes[j + 2] << 8) | bytes[j + 3];
    }
    for (let i = 16; i < 64; i++) {
      const a = w[i - 15];
      const b = w[i - 2];
      const s0 = rotr(a, 7) ^ rotr(a, 18) ^ (a >>> 3);
      const s1 = rotr(b, 17) ^ rotr(b, 19) ^ (b >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
    }
    let a = this.h[0], b = this.h[1], c = this.h[2], d = this.h[3];
    let e = this.h[4], f = this.h[5], g = this.h[6], h = this.h[7];
    for (let i = 0; i < 64; i++) {
      const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const t1 = (h + S1 + ch + K[i] + w[i]) | 0;
      const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (S0 + maj) | 0;
      h = g; g = f; f = e; e = (d + t1) | 0;
      d = c; c = b; b = a; a = (t1 + t2) | 0;
    }
    this.h[0] = (this.h[0] + a) | 0;
    this.h[1] = (this.h[1] + b) | 0;
    this.h[2] = (this.h[2] + c) | 0;
    this.h[3] = (this.h[3] + d) | 0;
    this.h[4] = (this.h[4] + e) | 0;
    this.h[5] = (this.h[5] + f) | 0;
    this.h[6] = (this.h[6] + g) | 0;
    this.h[7] = (this.h[7] + h) | 0;
  }

  update(data) {
    if (this.finished) throw new Error("this hash is already finished");
    const bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
    this.length += bytes.length;
    let at = 0;
    if (this.blockLen) {
      const need = 64 - this.blockLen;
      const take = Math.min(need, bytes.length);
      this.block.set(bytes.subarray(0, take), this.blockLen);
      this.blockLen += take;
      at = take;
      if (this.blockLen === 64) {
        this._compress(this.block, 0);
        this.blockLen = 0;
      }
    }
    while (at + 64 <= bytes.length) {
      this._compress(bytes, at);
      at += 64;
    }
    if (at < bytes.length) {
      this.block.set(bytes.subarray(at), 0);
      this.blockLen = bytes.length - at;
    }
    return this;
  }

  hex() {
    if (!this.finished) {
      const bitsHi = Math.floor(this.length / 0x20000000);
      const bitsLo = (this.length << 3) >>> 0;
      // The final block(s): the bytes already waiting, the 0x80 terminator,
      // zeros, then the message length in bits, big-endian.
      const tail = this.blockLen < 56 ? 56 : 120;
      const final = new Uint8Array(tail + 8);
      final.set(this.block.subarray(0, this.blockLen), 0);
      final[this.blockLen] = 0x80;
      final[tail] = (bitsHi >>> 24) & 255;
      final[tail + 1] = (bitsHi >>> 16) & 255;
      final[tail + 2] = (bitsHi >>> 8) & 255;
      final[tail + 3] = bitsHi & 255;
      final[tail + 4] = (bitsLo >>> 24) & 255;
      final[tail + 5] = (bitsLo >>> 16) & 255;
      final[tail + 6] = (bitsLo >>> 8) & 255;
      final[tail + 7] = bitsLo & 255;
      for (let at = 0; at < final.length; at += 64) this._compress(final, at);
      this.finished = true;
    }
    let out = "";
    for (let i = 0; i < 8; i++) {
      out += (this.h[i] >>> 0).toString(16).padStart(8, "0");
    }
    return out;
  }
}

export function sha256Hex(bytes) {
  return new Sha256().update(bytes).hex();
}
