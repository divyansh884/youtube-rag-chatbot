document.addEventListener("DOMContentLoaded", () => {
  const sendBtn = document.getElementById("send-btn");
  const userInput = document.getElementById("user-input");
  const chatBox = document.getElementById("chat-box");
  const loadingIndicator = document.getElementById("loading");

  // Allow pressing "Enter" to send
  userInput.addEventListener("keypress", (e) => {
    if (e.key === "Enter") {
      sendBtn.click();
    }
  });

  sendBtn.addEventListener("click", async () => {
    const message = userInput.value.trim();
    if (!message) return;

    // 1. Add user message to UI
    appendMessage(message, "user-msg");
    userInput.value = "";
    
    // 2. Show loading indicator and scroll to bottom
    loadingIndicator.style.display = "block";
    scrollToBottom();

    // 3. Get the current active tab's URL
    chrome.tabs.query({ active: true, currentWindow: true }, async (tabs) => {
      const currentUrl = tabs[0].url;

      // Ensure it's a YouTube video
      if (!currentUrl.includes("youtube.com/watch")) {
        loadingIndicator.style.display = "none";
        appendMessage("Please navigate to a YouTube video first.", "error-msg");
        return;
      }

      // 4. Send request to the Python backend
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
          throw new Error(`Server responded with status ${response.status}`);
        }

        const data = await response.json();
        
        // 5. Display the RAG response
        loadingIndicator.style.display = "none";
        appendMessage(data.answer, "bot-msg");

      } catch (error) {
        loadingIndicator.style.display = "none";
        appendMessage("Error: Ensure your Python backend is running on port 8000.", "error-msg");
        console.error(error);
      }
    });
  });

  // Helper function to add messages to the chat box
  function appendMessage(text, className) {
    const msgDiv = document.createElement("div");
    msgDiv.className = `message ${className}`;
    msgDiv.textContent = text;
    chatBox.appendChild(msgDiv);
    scrollToBottom();
  }

  function scrollToBottom() {
    chatBox.scrollTop = chatBox.scrollHeight;
  }
});