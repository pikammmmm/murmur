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

load();
