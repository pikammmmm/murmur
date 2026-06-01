// The Rust shell shows/hides this window and emits "murmur:state"; we just swap
// the pill's state class (idle | recording | transcribing). Registered after
// DOMContentLoaded because window.__TAURI__ isn't guaranteed populated for
// top-level scripts.
function start() {
  const bar = document.getElementById("bar");
  const ev = window.__TAURI__ && window.__TAURI__.event;
  if (!ev) {
    setTimeout(start, 100); // __TAURI__ not ready yet
    return;
  }
  ev.listen("murmur:state", (e) => {
    const s = e.payload;
    bar.className = s === "recording" || s === "transcribing" ? s : "idle";
  });
}

window.addEventListener("DOMContentLoaded", start);
