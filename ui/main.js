const { invoke } = window.__TAURI__.core;

let cfg = null;
const $ = (id) => document.getElementById(id);

function showStatus(msg, ms = 3000) {
  const s = $("status");
  s.textContent = msg;
  setTimeout(() => {
    if (s.textContent === msg) s.textContent = "";
  }, ms);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---- config ----
async function load() {
  try {
    cfg = await invoke("get_config");
    render();
  } catch (e) {
    showStatus("Failed to load settings: " + e, 6000);
  }
}

function render() {
  $("stt-provider").value = cfg.stt.provider;
  $("accuracy").checked = cfg.stt.accuracy_mode;
  $("fmt-provider").value = cfg.formatter.provider;
  $("fmt-mode").value = cfg.formatter.mode;
  $("voice-commands").checked = cfg.voice_commands !== false;
  $("audio-cues").checked = cfg.audio_cues !== false;
  $("inject-mode").value = cfg.inject_mode || "type";
  $("save-history").checked = cfg.save_history !== false;
  $("hotkey-key").value = cfg.hotkey.key;
  $("hotkey-side").value = cfg.hotkey.side;
  $("threshold").value = cfg.hotkey.hold_threshold_ms;
  $("k-groq").value = cfg.keys.groq || "";
  $("k-openai").value = cfg.keys.openai || "";
  $("k-anthropic").value = cfg.keys.anthropic || "";
  renderDict();
}

function renderDict() {
  const ul = $("dict");
  ul.innerHTML = "";
  (cfg.dictionary || []).forEach((term) => {
    const li = document.createElement("li");
    li.textContent = term;
    const btn = document.createElement("button");
    btn.textContent = "×";
    btn.onclick = async () => {
      try {
        cfg = await invoke("remove_dict_term", { term });
      } catch (e) {
        showStatus("Error: " + e);
      } finally {
        renderDict();
      }
    };
    li.appendChild(btn);
    ul.appendChild(li);
  });
}

function collect() {
  cfg.stt.provider = $("stt-provider").value;
  cfg.stt.accuracy_mode = $("accuracy").checked;
  cfg.formatter.provider = $("fmt-provider").value;
  cfg.formatter.mode = $("fmt-mode").value;
  cfg.voice_commands = $("voice-commands").checked;
  cfg.audio_cues = $("audio-cues").checked;
  cfg.inject_mode = $("inject-mode").value;
  cfg.save_history = $("save-history").checked;
  cfg.hotkey.key = $("hotkey-key").value;
  cfg.hotkey.side = $("hotkey-side").value;
  cfg.hotkey.hold_threshold_ms = parseInt($("threshold").value, 10) || 350;
  cfg.keys.groq = $("k-groq").value || null;
  cfg.keys.openai = $("k-openai").value || null;
  cfg.keys.anthropic = $("k-anthropic").value || null;
}

async function save() {
  collect();
  try {
    await invoke("set_config", { payload: cfg });
    showStatus("Saved ✓", 1500);
  } catch (e) {
    showStatus("Save failed: " + e, 5000);
  }
}

$("add-term").onclick = async () => {
  const input = $("new-term");
  const term = input.value.trim();
  if (!term) return;
  try {
    cfg = await invoke("add_dict_term", { term });
    input.value = "";
  } catch (e) {
    showStatus("Error: " + e);
  } finally {
    renderDict();
  }
};

$("save").onclick = save;

// ---- pronunciations / corrections ----
function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

async function loadCorrections() {
  let entries;
  try {
    entries = await invoke("get_corrections");
  } catch (e) {
    return;
  }
  const ul = $("corrections");
  ul.innerHTML = "";
  (entries || []).forEach((e) => {
    const li = document.createElement("li");
    const span = document.createElement("span");
    span.className = "pair";
    const src = e.source === "manual" ? " · manual" : "";
    span.innerHTML = `${escapeHtml(e.wrong)} &rarr; <b>${escapeHtml(e.right)}</b> <span class="cnt">&times;${e.count || 1}${src}</span>`;
    const btn = document.createElement("button");
    btn.textContent = "×";
    btn.onclick = async () => {
      try {
        await invoke("remove_correction", { wrong: e.wrong });
      } catch (err) {
        showStatus("Error: " + err);
      }
      refreshCorrections();
    };
    li.appendChild(span);
    li.appendChild(btn);
    ul.appendChild(li);
  });
}

// Mutations go to the sidecar (single writer); re-read a few times as it settles.
function refreshCorrections() {
  [150, 450, 900].forEach((d) => setTimeout(loadCorrections, d));
}

$("add-corr").onclick = async () => {
  const wrong = $("corr-wrong").value.trim();
  const right = $("corr-right").value.trim();
  if (!wrong || !right) return;
  try {
    await invoke("add_correction", { wrong, right });
    $("corr-wrong").value = "";
    $("corr-right").value = "";
  } catch (e) {
    showStatus("Error: " + e);
  }
  refreshCorrections();
};

async function reloadLast() {
  try {
    $("teach-box").value = (await invoke("get_last_raw")) || "";
  } catch (e) {
    /* ignore */
  }
}

$("teach-reload").onclick = reloadLast;

$("teach-save").onclick = async () => {
  const text = $("teach-box").value.trim();
  if (!text) return;
  try {
    await invoke("teach_last", { text });
    showStatus("Taught ✓", 1500);
  } catch (e) {
    showStatus("Error: " + e);
  }
  refreshCorrections();
};

// ---- "try it" preview (poll until the sidecar's result settles) ----
$("preview-run").onclick = async () => {
  const text = $("preview-in").value;
  if (!text.trim()) return;
  const out = $("preview-out");
  out.textContent = "…";
  let before = "";
  try {
    before = await invoke("get_preview");
  } catch (e) {
    /* ignore */
  }
  try {
    await invoke("do_preview", { text });
  } catch (e) {
    out.textContent = "error: " + e;
    return;
  }
  for (let i = 0; i < 15; i++) {
    await sleep(100);
    let cur = before;
    try {
      cur = await invoke("get_preview");
    } catch (e) {
      /* keep polling */
    }
    if (cur !== before) {
      out.textContent = cur;
      return;
    }
  }
  out.textContent = before || "(no response)";
};

// ---- history & stats ----
async function loadHistory() {
  let stats = {};
  let hist = [];
  try {
    [stats, hist] = await Promise.all([invoke("get_stats"), invoke("get_history")]);
  } catch (e) {
    return;
  }
  const words = stats.words || 0;
  const dictations = stats.dictations || 0;
  const minSaved = Math.round(words * (1 / 40 - 1 / 150));
  $("stats-line").textContent = `${dictations} dictations · ${words} words · ~${minSaved} min saved vs typing`;
  const ul = $("history");
  ul.innerHTML = "";
  (hist || []).forEach((e) => {
    const li = document.createElement("li");
    li.className = "hist";
    li.textContent = (e.text || "").slice(0, 120) || "(empty)";
    ul.appendChild(li);
  });
}

function refreshHistory() {
  [150, 450, 900].forEach((d) => setTimeout(loadHistory, d));
}

$("clear-history").onclick = async () => {
  try {
    await invoke("clear_history");
  } catch (e) {
    showStatus("Error: " + e);
  }
  refreshHistory();
};

// ---- autostart (registry-backed, independent of the config save) ----
async function loadAutostart() {
  try {
    $("autostart").checked = await invoke("get_autostart");
  } catch (e) {
    /* ignore */
  }
}
$("autostart").onchange = async () => {
  try {
    await invoke("set_autostart", { enabled: $("autostart").checked });
  } catch (e) {
    showStatus("Autostart failed: " + e);
  }
};

load();
loadCorrections();
reloadLast();
loadHistory();
loadAutostart();
