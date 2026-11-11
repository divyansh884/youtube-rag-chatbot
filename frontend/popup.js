document.addEventListener("DOMContentLoaded", () => {
  const sendBtn = document.getElementById("send-btn");
  const userInput = document.getElementById("user-input");
  const chatBox = document.getElementById("chat-box");
  const clearBtn = document.getElementById("clear-btn");
  const statusDot = document.getElementById("status-indicator");
  const statusText = document.getElementById("status-text");
  const tabWarning = document.getElementById("tab-warning");
  const quickPrompts = document.getElementById("quick-prompts");

  // Configure Marked if available
  if (window.marked) {
    marked.setOptions({
      breaks: true,
      gfm: true
    });
  }

  // Check current active tab on popup open
  let activeVideoUrl = null;
  checkActiveTab();

  function checkActiveTab() {
    if (!chrome.tabs) return;

    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (!tabs || !tabs[0]) return;
      const url = tabs[0].url || "";

      if (url.includes("youtube.com/watch")) {
        activeVideoUrl = url;
        statusDot.classList.remove("offline");
        statusText.textContent = "Video Detected";
        tabWarning.style.display = "none";
      } else {
        activeVideoUrl = null;
        statusDot.classList.add("offline");
        statusText.textContent = "No Video";
        tabWarning.style.display = "flex";
      }
    });
  }

  // Allow pressing "Enter" to send
  userInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  });

  sendBtn.addEventListener("click", () => {
    handleSend();
  });

  // Quick Prompt Chips
  if (quickPrompts) {
    quickPrompts.addEventListener("click", (e) => {
      const chip = e.target.closest(".chip-btn");
      if (!chip) return;
      const prompt = chip.getAttribute("data-prompt");
      if (prompt) {
        userInput.value = prompt;
        handleSend();
      }
    });
  }

  // Clear Chat History
  clearBtn.addEventListener("click", () => {
    chatBox.innerHTML = `
      <div class="message-row bot-row">
        <div class="bot-card">
          <div class="bot-card-header">
            <div class="bot-card-title">
              <span>⚡ AI Assistant</span>
            </div>
          </div>
          <div class="bot-content">
            <p>Chat cleared! Ask me anything about this YouTube video.</p>
          </div>
        </div>
      </div>
    `;
  });

  async function handleSend() {
    const message = userInput.value.trim();
    if (!message) return;

    // Check YouTube video tab
    if (!chrome.tabs) {
      appendError("Chrome tabs API unavailable.");
      return;
    }

    chrome.tabs.query({ active: true, currentWindow: true }, async (tabs) => {
      const currentUrl = tabs && tabs[0] ? tabs[0].url : "";

      if (!currentUrl || !currentUrl.includes("youtube.com/watch")) {
        tabWarning.style.display = "flex";
        appendError("Please navigate to a YouTube video first.");
        return;
      }

      tabWarning.style.display = "none";

      // 1. Add user message
      appendUserMessage(message);
      userInput.value = "";
      setLoading(true);

      // 2. Send request to backend
      try {
        const response = await fetch("http://127.0.0.1:8000/chat", {
          method: "POST",
          headers: {
            "Content-Type": "application/json"
          },
          body: JSON.stringify({
            url: currentUrl,
            message: message
          })
        });

        if (!response.ok) {
          const errData = await response.json().catch(() => null);
          const detail = errData && errData.detail ? errData.detail : `HTTP ${response.status}`;
          throw new Error(detail);
        }

        const data = await response.json();
        setLoading(false);

        // 3. Render Markdown Bot Response
        appendBotMessage(data.answer);

      } catch (error) {
        setLoading(false);
        console.error("Backend error:", error);
        appendError(
          `Error: ${error.message || "Failed to reach backend."} Ensure your backend server is running on http://127.0.0.1:8000.`
        );
      }
    });
  }

  // Append User Bubble
  function appendUserMessage(text) {
    const row = document.createElement("div");
    row.className = "message-row user-row";

    const bubble = document.createElement("div");
    bubble.className = "user-bubble";
    bubble.textContent = text;

    row.appendChild(bubble);
    chatBox.appendChild(row);
    scrollToBottom();
  }

  // Append Bot Card with Markdown Formatting & Copy Button
  function appendBotMessage(markdownText) {
    const row = document.createElement("div");
    row.className = "message-row bot-row";

    const card = document.createElement("div");
    card.className = "bot-card";

    // Header bar with copy button
    const header = document.createElement("div");
    header.className = "bot-card-header";

    const title = document.createElement("div");
    title.className = "bot-card-title";
    title.innerHTML = `<span>⚡ AI Assistant</span>`;

    const copyBtn = document.createElement("button");
    copyBtn.className = "copy-btn";
    copyBtn.innerHTML = `
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
        <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"></path>
      </svg>
      <span>Copy</span>
    `;

    copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(markdownText);
        copyBtn.innerHTML = `
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#16a34a" stroke-width="2.5">
            <polyline points="20 6 9 17 4 12"></polyline>
          </svg>
          <span style="color:#16a34a;">Copied!</span>
        `;
        setTimeout(() => {
          copyBtn.innerHTML = `
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
              <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"></path>
            </svg>
            <span>Copy</span>
          `;
        }, 1800);
      } catch (e) {
        console.error("Clipboard copy failed:", e);
      }
    });

    header.appendChild(title);
    header.appendChild(copyBtn);

    // Formatted content
    const content = document.createElement("div");
    content.className = "bot-content";
    content.innerHTML = renderMarkdown(markdownText);

    card.appendChild(header);
    card.appendChild(content);
    row.appendChild(card);
    chatBox.appendChild(row);
    scrollToBottom();
  }

  // Append Error Notice
  function appendError(errorMessage) {
    const errorCard = document.createElement("div");
    errorCard.className = "error-card";
    errorCard.innerHTML = `
      <span>⚠️</span>
      <div>${escapeHtml(errorMessage)}</div>
    `;
    chatBox.appendChild(errorCard);
    scrollToBottom();
  }

  // Loading State with Animated Dots
  function setLoading(isLoading) {
    sendBtn.disabled = isLoading;
    userInput.disabled = isLoading;

    const existingTyping = document.getElementById("typing-indicator");
    if (isLoading && !existingTyping) {
      const typingRow = document.createElement("div");
      typingRow.className = "message-row bot-row";
      typingRow.id = "typing-indicator";
      typingRow.innerHTML = `
        <div class="typing-card">
          <span>Thinking</span>
          <div class="dot"></div>
          <div class="dot"></div>
          <div class="dot"></div>
        </div>
      `;
      chatBox.appendChild(typingRow);
      scrollToBottom();
    } else if (!isLoading && existingTyping) {
      existingTyping.remove();
      userInput.focus();
    }
  }

  // Preprocess markdown to fix run-on bullet points or squashed list markers
  function preprocessMarkdown(text) {
    if (!text) return "";
    let cleaned = text.replace(/([^\n])\s*\*\s+\*\*/g, "$1\n\n- **");
    cleaned = cleaned.replace(/([^\n])\s*(\d+\.)\s+/g, "$1\n\n$2 ");
    return cleaned;
  }

  // Safe Markdown -> Sanitized HTML
  function renderMarkdown(rawText) {
    if (!rawText) return "";
    const preparedText = preprocessMarkdown(rawText);
    try {
      if (window.marked && window.DOMPurify) {
        const rawHtml = marked.parse(preparedText);
        return DOMPurify.sanitize(rawHtml);
      } else if (window.marked) {
        return marked.parse(preparedText);
      }
    } catch (err) {
      console.warn("Markdown parsing fallback:", err);
    }
    // Fallback if marked fails or is missing
    return escapeHtml(preparedText).replace(/\n/g, "<br>");
  }

  function escapeHtml(str) {
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function scrollToBottom() {
    chatBox.scrollTop = chatBox.scrollHeight;
  }
});
