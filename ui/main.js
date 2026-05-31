const { invoke } = window.__TAURI__.core;

let cfg = null;

const $ = (id) => document.getElementById(id);

async function load() {
  cfg = await invoke("get_config");
  render();
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
    btn.title = "Remove";
    btn.onclick = async () => {
      cfg = await invoke("remove_dict_term", { term });
      renderDict();
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
  await invoke("set_config", { payload: cfg });
  const s = $("status");
  s.textContent = "Saved ✓";
  setTimeout(() => (s.textContent = ""), 1500);
}

$("add-term").onclick = async () => {
  const input = $("new-term");
  const term = input.value.trim();
  if (term) {
    cfg = await invoke("add_dict_term", { term });
    input.value = "";
    renderDict();
  }
};

$("save").onclick = save;

// ---- pronunciations / corrections ----
function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

async function loadCorrections() {
  const entries = await invoke("get_corrections");
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
    btn.title = "Remove";
    btn.onclick = async () => {
      await invoke("remove_correction", { wrong: e.wrong });
      refreshCorrections();
    };
    li.appendChild(span);
    li.appendChild(btn);
    ul.appendChild(li);
  });
}

// Mutations go to the sidecar (single writer); re-read after it persists+emits.
function refreshCorrections() {
  setTimeout(loadCorrections, 250);
}

$("add-corr").onclick = async () => {
  const wrong = $("corr-wrong").value.trim();
  const right = $("corr-right").value.trim();
  if (wrong && right) {
    await invoke("add_correction", { wrong, right });
    $("corr-wrong").value = "";
    $("corr-right").value = "";
    refreshCorrections();
  }
};

async function reloadLast() {
  $("teach-box").value = (await invoke("get_last_raw")) || "";
}

$("teach-reload").onclick = reloadLast;

$("teach-save").onclick = async () => {
  const text = $("teach-box").value.trim();
  if (text) {
    await invoke("teach_last", { text });
    const s = $("status");
    s.textContent = "Taught ✓";
    setTimeout(() => (s.textContent = ""), 1500);
    refreshCorrections();
  }
};

// ---- history & stats ----
async function loadHistory() {
  const [stats, hist] = await Promise.all([invoke("get_stats"), invoke("get_history")]);
  const words = stats.words || 0;
  const dictations = stats.dictations || 0;
  const minSaved = Math.round(words * (1 / 40 - 1 / 150));
  $("stats-line").textContent = `${dictations} dictations · ${words} words · ~${minSaved} min saved vs typing`;
  const ul = $("history");
  ul.innerHTML = "";
  (hist || []).forEach((e) => {
    const li = document.createElement("li");
    const text = (e.text || "").slice(0, 120);
    li.className = "hist";
    li.textContent = text || "(empty)";
    ul.appendChild(li);
  });
}

$("clear-history").onclick = async () => {
  await invoke("clear_history");
  setTimeout(loadHistory, 250);
};

// ---- "try it" preview ----
$("preview-run").onclick = async () => {
  const text = $("preview-in").value;
  if (!text.trim()) return;
  await invoke("do_preview", { text });
  setTimeout(async () => {
    $("preview-out").textContent = await invoke("get_preview");
  }, 200);
};

// ---- autostart (registry-backed, independent of the config save) ----
async function loadAutostart() {
  $("autostart").checked = await invoke("get_autostart");
}
$("autostart").onchange = () => invoke("set_autostart", { enabled: $("autostart").checked });

load();
loadCorrections();
reloadLast();
loadHistory();
loadAutostart();
