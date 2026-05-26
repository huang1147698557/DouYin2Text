const modeSwitch = document.getElementById("modeSwitch");
const modeField = document.getElementById("modeField");
const urlBlock = document.getElementById("urlBlock");
const fileBlock = document.getElementById("fileBlock");
const fileInput = document.getElementById("fileInput");
const fileName = document.getElementById("fileName");
const dropzone = document.getElementById("dropzone");
const asrProviderSelect = document.getElementById("asrProviderSelect");
const whisperModelField = document.getElementById("whisperModelField");
const whisperModelSelect = document.getElementById("whisperModelSelect");
const clearButton = document.getElementById("clearButton");
const extractForm = document.getElementById("extractForm");
const submitButton = document.getElementById("submitButton");
const resultShell = document.getElementById("resultShell");
const resultTitle = document.getElementById("resultTitle");
const rawDesc = document.getElementById("rawDesc");
const asrParagraphs = document.getElementById("asrParagraphs");
const metadataBox = document.getElementById("metadataBox");
const metaChips = document.getElementById("metaChips");
const videoPlayer = document.getElementById("videoPlayer");
const videoPlaceholder = document.getElementById("videoPlaceholder");
const videoLink = document.getElementById("videoLink");
const coverLink = document.getElementById("coverLink");
const historyDrawer = document.getElementById("historyDrawer");
const historyToggle = document.getElementById("historyToggle");
const historyClose = document.getElementById("historyClose");
const historyList = document.getElementById("historyList");
const settingsDrawer = document.getElementById("settingsDrawer");
const settingsToggle = document.getElementById("settingsToggle");
const settingsClose = document.getElementById("settingsClose");
const settingsForm = document.getElementById("settingsForm");
const resetSettingsButton = document.getElementById("resetSettingsButton");
const copyAsrButton = document.getElementById("copyAsrButton");
const toast = document.getElementById("toast");

let currentAsrText = "";

function showToast(message) {
  toast.textContent = message;
  toast.classList.remove("hidden");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.classList.add("hidden");
  }, 3200);
}

function setMode(mode) {
  modeField.value = mode;
  for (const pill of modeSwitch.querySelectorAll(".mode-pill")) {
    pill.classList.toggle("active", pill.dataset.mode === mode);
  }
  urlBlock.classList.toggle("hidden", mode !== "url");
  fileBlock.classList.toggle("hidden", mode !== "file");
}

function renderParagraphs(rawText, paragraphs = []) {
  const blocks = paragraphs.length ? paragraphs : rawText ? [rawText] : [];
  asrParagraphs.innerHTML = "";
  if (!blocks.length) {
    asrParagraphs.innerHTML = "<p>未识别到内容</p>";
    return;
  }
  for (const paragraph of blocks) {
    const p = document.createElement("p");
    p.textContent = paragraph;
    asrParagraphs.appendChild(p);
  }
}

function formatRequestedProvider(value) {
  if (value === "volcengine") {
    return "火山模型";
  }
  if (value === "whisper") {
    return "Whisper";
  }
  if (value === "auto") {
    return "自动";
  }
  return value;
}

function syncAsrFields() {
  const useWhisper = asrProviderSelect.value === "whisper";
  whisperModelField.classList.toggle("hidden", !useWhisper);
  whisperModelSelect.disabled = !useWhisper;
}

function setMediaLinks(data) {
  if (data.video_url) {
    videoPlayer.src = data.video_url;
    videoPlayer.classList.remove("hidden");
    videoPlaceholder.classList.add("hidden");
    videoLink.href = data.video_url;
    videoLink.classList.remove("hidden");
  } else {
    videoPlayer.removeAttribute("src");
    videoPlayer.load();
    videoPlaceholder.classList.remove("hidden");
    videoLink.classList.add("hidden");
  }

  if (data.cover_url) {
    coverLink.href = data.cover_url;
    coverLink.classList.remove("hidden");
  } else {
    coverLink.classList.add("hidden");
  }
}

function setChips(data) {
  metaChips.innerHTML = "";
  const chips = [
    data.mode === "url" ? "链接模式" : "文件模式",
    data.requested_asr_provider ? `选择: ${formatRequestedProvider(data.requested_asr_provider)}` : null,
    data.asr_provider ? `ASR: ${data.asr_provider}` : null,
    data.whisper_model ? `Whisper: ${data.whisper_model}` : null,
    data.author ? `作者: ${data.author}` : null,
    data.mcp_transport ? `MCP: ${data.mcp_transport}` : null,
  ].filter(Boolean);

  for (const chip of chips) {
    const span = document.createElement("span");
    span.className = "chip";
    span.textContent = chip;
    metaChips.appendChild(span);
  }
}

function renderResult(data) {
  resultShell.classList.remove("hidden");
  resultTitle.textContent = data.title || "提取完成";
  rawDesc.textContent = data.raw_desc || "无原始文案";
  renderParagraphs(data.asr_raw_text, data.asr_paragraphs || []);
  currentAsrText = data.asr_raw_text || "";
  setChips(data);
  setMediaLinks(data);
  metadataBox.textContent = JSON.stringify(data.video_metadata || {}, null, 2);
}

async function loadHistory() {
  const response = await fetch("/api/history");
  const payload = await response.json();
  historyList.innerHTML = "";

  if (!payload.items?.length) {
    historyList.innerHTML = '<div class="history-item"><p>还没有历史记录。</p></div>';
    return;
  }

  for (const item of payload.items) {
    const card = document.createElement("article");
    card.className = "history-item";
    card.innerHTML = `
      <h3>${item.title || "未命名结果"}</h3>
      <p>${item.created_at || ""}</p>
      <p>${item.raw_desc || item.asr_raw_text?.slice(0, 80) || "无摘要"}</p>
    `;
    card.addEventListener("click", () => {
      renderResult(item);
      historyDrawer.classList.remove("open");
    });
    historyList.appendChild(card);
  }
}

modeSwitch.addEventListener("click", (event) => {
  const button = event.target.closest(".mode-pill");
  if (!button) return;
  setMode(button.dataset.mode);
});

clearButton.addEventListener("click", () => {
  extractForm.reset();
  fileName.textContent = "尚未选择文件";
  setMode("url");
  syncAsrFields();
});

fileInput.addEventListener("change", () => {
  const file = fileInput.files?.[0];
  fileName.textContent = file ? file.name : "尚未选择文件";
});

["dragenter", "dragover"].forEach((type) => {
  dropzone.addEventListener(type, (event) => {
    event.preventDefault();
    dropzone.classList.add("dragover");
  });
});

["dragleave", "drop"].forEach((type) => {
  dropzone.addEventListener(type, (event) => {
    event.preventDefault();
    dropzone.classList.remove("dragover");
  });
});

dropzone.addEventListener("drop", (event) => {
  const file = event.dataTransfer?.files?.[0];
  if (!file) return;
  fileInput.files = event.dataTransfer.files;
  fileName.textContent = file.name;
});

asrProviderSelect.addEventListener("change", () => {
  syncAsrFields();
});

historyToggle.addEventListener("click", async () => {
  historyDrawer.classList.add("open");
  await loadHistory();
});

historyClose.addEventListener("click", () => {
  historyDrawer.classList.remove("open");
});

// Settings drawer
settingsToggle.addEventListener("click", async () => {
  settingsDrawer.classList.add("open");
  await loadSettings();
});

settingsClose.addEventListener("click", () => {
  settingsDrawer.classList.remove("open");
});

async function loadSettings() {
  try {
    const response = await fetch("/api/settings");
    const data = await response.json();
    document.getElementById("appIdInput").value = data.app_id || "";
    document.getElementById("tokenInput").value = data.access_token || "";
    document.getElementById("apiKeyInput").value = data.api_key || "";
    document.getElementById("uidInput").value = data.uid || "";
  } catch (error) {
    console.error("Failed to load settings:", error);
  }
}

settingsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  
  const formData = {
    app_id: document.getElementById("appIdInput").value.trim(),
    access_token: document.getElementById("tokenInput").value.trim(),
    api_key: document.getElementById("apiKeyInput").value.trim(),
    uid: document.getElementById("uidInput").value.trim(),
  };
  
  try {
    const response = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(formData),
    });
    
    if (!response.ok) {
      throw new Error("保存失败");
    }
    
    showToast("配置保存成功");
    settingsDrawer.classList.remove("open");
  } catch (error) {
    showToast(error.message || "保存配置失败");
  }
});

resetSettingsButton.addEventListener("click", () => {
  document.getElementById("appIdInput").value = "";
  document.getElementById("tokenInput").value = "";
  document.getElementById("apiKeyInput").value = "";
  document.getElementById("uidInput").value = "";
});

// Copy ASR text
copyAsrButton.addEventListener("click", async () => {
  if (!currentAsrText) {
    showToast("没有可复制的内容");
    return;
  }
  
  try {
    await navigator.clipboard.writeText(currentAsrText);
    copyAsrButton.textContent = "已复制！";
    copyAsrButton.classList.add("copied");
    
    setTimeout(() => {
      copyAsrButton.textContent = "复制";
      copyAsrButton.classList.remove("copied");
    }, 2000);
  } catch (error) {
    showToast("复制失败");
  }
});

extractForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(extractForm);

  submitButton.disabled = true;
  submitButton.textContent = "处理中...";

  try {
    const response = await fetch("/api/extract", {
      method: "POST",
      body: formData,
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "处理失败");
    }
    renderResult(payload);
    await loadHistory();
  } catch (error) {
    showToast(error.message || "请求失败");
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "开始提取";
  }
});

setMode("url");
syncAsrFields();
