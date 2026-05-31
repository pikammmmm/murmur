// The Rust shell shows/hides this window and emits "murmur:state"; we just swap
// the blob's animation class. Registered after DOMContentLoaded because
// window.__TAURI__ isn't guaranteed populated for top-level scripts.
function start() {
  const blob = document.getElementById("blob");
  const ev = window.__TAURI__ && window.__TAURI__.event;
  if (!ev) {
    setTimeout(start, 100); // __TAURI__ not ready yet
    return;
  }
  ev.listen("murmur:state", (e) => {
    const s = e.payload;
    blob.className = s === "recording" || s === "transcribing" ? s : "idle";
  });
}

window.addEventListener("DOMContentLoaded", start);
