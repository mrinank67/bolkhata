/**
 * Client-side downscale + compression for gallery-picked photos.
 *
 * Two reasons this exists even though the server re-encodes everything:
 *   1. A raw phone photo is routinely 3-8 MB, over the server's 3 MB cap.
 *   2. Shops run on 3G/4G — 90 KB uploads in about a second, 6 MB does not.
 *
 * It is NOT a security control. The server treats the output as hostile input
 * and re-decodes/re-encodes it (see image_utils.py). Photos taken with the
 * in-app camera skip this entirely — captureFrame() already sizes them.
 */

import { toBlobWithFallback } from "./camera.js";

const MAX_EDGE = 1024;
const QUALITY = 0.82;

export async function compressImage(file, { maxEdge = MAX_EDGE, quality = QUALITY } = {}) {
  const bitmap = await loadBitmap(file);

  const scale = Math.min(1, maxEdge / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  if (bitmap.close) bitmap.close();

  return toBlobWithFallback(canvas, quality);
}

async function loadBitmap(file) {
  if (window.createImageBitmap) {
    // imageOrientation must be explicit: the historical default was "none",
    // which bakes a sideways photo into the canvas. The server strips EXIF
    // after this point, so it can no longer correct an orientation we lose here.
    return createImageBitmap(file, { imageOrientation: "from-image" });
  }

  // Older Safari — fall back to an <img> off an object URL.
  const url = URL.createObjectURL(file);
  try {
    return await new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error("decode-failed"));
      img.src = url;
    });
  } finally {
    URL.revokeObjectURL(url);
  }
}
