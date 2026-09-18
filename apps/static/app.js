const messages = document.querySelector("#messages");
const form = document.querySelector("#chat-form");
const input = document.querySelector("#message-input");
const sendButton = document.querySelector("#send-button");
const apiKeyInput = document.querySelector("#api-key");
const providerInput = document.querySelector("#provider");
const modelInput = document.querySelector("#model-name");
const baseUrlInput = document.querySelector("#base-url");
const customUrlRow = document.querySelector("#custom-url-row");
const applyModelButton = document.querySelector("#apply-model");
const modelStatus = document.querySelector("#model-status");
const modeBadge = document.querySelector("#mode-badge");
const phases = ["VERIFY_ID", "RESOLVE_INTENT", "PROCESS_CASE", "POST_PROCESS"];
const defaultModels = {
  deepseek: "deepseek-flash",
  openai: "gpt-5-mini",
};

let sessionId = null;
let modelSettings = {
  provider: "deepseek",
  apiKey: null,
  model: "deepseek-flash",
  baseUrl: null,
};

function addMessage(role, content) {
  const wrapper = document.createElement("article");
  wrapper.className = `message ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = content;
  const label = document.createElement("small");
  label.textContent = role === "agent" ? "Claims Guide" : "You";
  wrapper.append(bubble, label);
  messages.append(wrapper);
  messages.scrollTop = messages.scrollHeight;
}

function setPhase(currentPhase) {
  const currentIndex = phases.indexOf(currentPhase);
  document.querySelectorAll("#phase-list li").forEach((item) => {
    const index = phases.indexOf(item.dataset.phase);
    item.classList.toggle("active", index === currentIndex);
    item.classList.toggle("complete", index < currentIndex);
  });
}

async function newConversation() {
  messages.replaceChildren();
  modeBadge.textContent = "Starting…";
  const response = await fetch("/api/sessions", { method: "POST" });
  if (!response.ok) throw new Error("Could not start a conversation.");
  const data = await response.json();
  sessionId = data.session_id;
  addMessage("agent", data.message);
  setPhase(data.state.phase);
  modeBadge.textContent = "Ready";
  input.focus();
}

async function sendMessage(message) {
  addMessage("user", message);
  sendButton.disabled = true;
  input.disabled = true;
  modeBadge.textContent = "Thinking…";
  let focusMessageInput = true;
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        message,
        api_key: modelSettings.apiKey,
        provider: modelSettings.provider,
        model: modelSettings.model,
        base_url: modelSettings.baseUrl,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "The request failed.");
    addMessage("agent", data.message);
    setPhase(data.state.phase);
    if (data.needs_api_key) {
      modeBadge.textContent = "API key required";
      const settings = document.querySelector(".settings");
      settings.open = true;
      focusMessageInput = false;
      apiKeyInput.focus();
    } else {
      modeBadge.textContent = data.model_used ? "AI connected" : "Model unavailable";
    }
  } catch (error) {
    addMessage("agent", `Sorry, something went wrong: ${error.message}`);
    modeBadge.textContent = "Error";
  } finally {
    sendButton.disabled = false;
    input.disabled = false;
    if (focusMessageInput) input.focus();
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message || !sessionId) return;
  input.value = "";
  input.style.height = "auto";
  sendMessage(message);
});

input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 130)}px`;
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

document.querySelector("#sample-message").addEventListener("click", () => {
  input.value = "I'm the policyholder. My name is Margaret Chen, policy POL-9921. I'm calling about my denied healthcare claim from January. DOB is 1985-03-15, SSN last four is 4472.";
  input.dispatchEvent(new Event("input"));
  input.focus();
});

document.querySelector("#new-chat").addEventListener("click", newConversation);

providerInput.addEventListener("change", () => {
  customUrlRow.hidden = providerInput.value !== "custom";
  if (!modelInput.value.trim()) {
    if (providerInput.value === "deepseek") modelInput.placeholder = "deepseek-flash";
    else if (providerInput.value === "openai") modelInput.placeholder = "gpt-5-mini";
    else modelInput.placeholder = "Uses provider default";
  }
  modelStatus.textContent = "";
  modelStatus.classList.remove("error");
});

applyModelButton.addEventListener("click", () => {
  const provider = providerInput.value;
  const apiKey = apiKeyInput.value.trim();
  const model = modelInput.value.trim();
  const baseUrl = baseUrlInput.value.trim();
  const selectedModel = model || defaultModels[provider] || null;

  modelStatus.classList.remove("error");
  if (provider === "server" && apiKey) {
    modelStatus.textContent = "Choose DeepSeek, OpenAI, or a compatible API when entering your own key.";
    modelStatus.classList.add("error");
    providerInput.focus();
    return;
  }
  if (provider !== "server" && !apiKey) {
    modelStatus.textContent = "Enter an API key for the selected provider.";
    modelStatus.classList.add("error");
    apiKeyInput.focus();
    return;
  }
  if (provider === "custom" && (!baseUrl || !model)) {
    modelStatus.textContent = "A custom provider needs both an HTTPS base URL and model name.";
    modelStatus.classList.add("error");
    return;
  }

  modelSettings = {
    provider,
    apiKey: apiKey || null,
    model: selectedModel,
    baseUrl: provider === "custom" ? baseUrl : null,
  };

  const providerLabel = providerInput.options[providerInput.selectedIndex].text;
  const modelDescription = selectedModel ? ` using ${selectedModel}` : "";
  modelStatus.textContent = `${providerLabel} settings applied${modelDescription}.`;
  modeBadge.textContent = provider === "server" ? "Server model" : `${providerLabel} configured`;
  input.focus();
});

newConversation().catch((error) => addMessage("agent", error.message));
