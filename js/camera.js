/**
 * In-app camera — live preview inside the page, capture straight to memory.
 *
 * Deliberately NOT <input type="file" capture="environment">: that hands off to
 * the OS camera app, which on Android writes the photo into DCIM/Camera before
 * returning it. Nothing the shopkeeper captures here should land on the device,
 * so we stream getUserMedia into a <video> and draw the frame to an off-screen
 * canvas. The result only ever exists as an in-memory Blob.
 *
 * Requires a secure context — navigator.mediaDevices is undefined on plain
 * http:// (localhost excepted), so this silently needs HTTPS in production.
 */

const MAX_EDGE = 1024;
const QUALITY = 0.82;

let stream = null;

export function isCameraSupported() {
  return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
}

export async function startCamera(videoEl) {
  stopCamera(videoEl); // never leave a previous stream running
  stream = await navigator.mediaDevices.getUserMedia({
    video: {
      facingMode: { ideal: "environment" }, // rear camera for product shots
      width: { ideal: 1280 },
      height: { ideal: 1280 }
    },
    audio: false
  });
  videoEl.srcObject = stream;
  await videoEl.play();
}

export function stopCamera(videoEl) {
  // Stopping every track is what releases the camera and turns off the
  // hardware indicator light — without it the light lingers after the modal
  // closes and looks like the app is still watching.
  if (stream) {
    stream.getTracks().forEach(track => track.stop());
    stream = null;
  }
  if (videoEl) videoEl.srcObject = null;
}

export function isCameraRunning() {
  return stream !== null;
}

/** Draw the current video frame to a canvas and return it as a Blob. */
export function captureFrame(videoEl, { maxEdge = MAX_EDGE, quality = QUALITY } = {}) {
  const vw = videoEl.videoWidth;
  const vh = videoEl.videoHeight;
  if (!vw || !vh) return Promise.reject(new Error("camera-not-ready"));

  const scale = Math.min(1, maxEdge / Math.max(vw, vh));
  const canvas = document.createElement("canvas"); // local — GC'd, never persisted
  canvas.width = Math.round(vw * scale);
  canvas.height = Math.round(vh * scale);
  canvas.getContext("2d").drawImage(videoEl, 0, 0, canvas.width, canvas.height);

  return toBlobWithFallback(canvas, quality);
}

/**
 * Safari < 14 silently ignores an unsupported toBlob type and hands back a PNG,
 * which is larger than the JPEG we started from. Detect via blob.type and retry.
 */
export async function toBlobWithFallback(canvas, quality) {
  let blob = await new Promise(resolve => canvas.toBlob(resolve, "image/webp", quality));
  if (!blob || blob.type !== "image/webp") {
    blob = await new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", quality));
  }
  if (!blob) throw new Error("encode-failed");
  return blob;
}

/** Human-readable reason for a getUserMedia rejection. */
export function cameraErrorMessage(err) {
  switch (err && err.name) {
    case "NotAllowedError":
    case "SecurityError":
      return "Camera permission denied. Pick a photo from the gallery instead.";
    case "NotFoundError":
    case "OverconstrainedError":
      return "No camera found. Pick a photo from the gallery instead.";
    case "NotReadableError":
      return "Camera is busy in another app. Close it and try again.";
    default:
      return "Could not open the camera. Pick a photo from the gallery instead.";
  }
}
