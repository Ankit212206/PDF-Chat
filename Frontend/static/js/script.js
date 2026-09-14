window.appendMessage = function(sender, text) {
    const chatBox = document.getElementById("chat-box");
    if (!chatBox) return;

    const wrapper = document.createElement("div");
    wrapper.className = `d-flex mb-3 ${sender === 'user' ? 'justify-content-end' : 'justify-content-start'}`;

    const bubble = document.createElement("div");
    bubble.className = `p-3 rounded-3 shadow-sm ${sender === 'user' ? 'bg-primary text-white' : 'bg-white text-dark'}`;
    bubble.style.maxWidth = "85%";

    if (sender === 'user') {
        const pre = document.createElement("pre");
        pre.style.whiteSpace = "pre-wrap";
        pre.style.margin = "0";
        pre.style.fontFamily = 'inherit';
        pre.textContent = text;
        bubble.appendChild(pre);
    } else {
        const contentDiv = document.createElement("div");
        contentDiv.className = "markdown-content";
        contentDiv.innerHTML = marked.parse(text);
        bubble.appendChild(contentDiv);
    }

    wrapper.appendChild(bubble);
    chatBox.appendChild(wrapper);
    chatBox.scrollTop = chatBox.scrollHeight;
};

document.addEventListener("DOMContentLoaded", () => {
    const chatForm = document.getElementById("chat-form");
    const userInput = document.getElementById("user-input");
    const chatBox = document.getElementById("chat-box");
    const sendBtn = document.getElementById("send-btn");
    
    if (!chatForm) return; 

    const chatUrl = chatForm.getAttribute("data-chat-url");

    async function loadActiveChat() {
        try {
            const res = await fetch("/api/load_chat/active");
            if (res.ok) {
                const data = await res.json();
                if (data.chat && data.chat.length > 0) {
                    chatBox.innerHTML = "";
                    data.chat.forEach(msg => window.appendMessage(msg.sender, msg.text));
                }
            }
        } catch (e) {
            console.error("Error loading active chat:", e);
        }
    }

    loadActiveChat();

    chatForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const query = userInput.value.trim();
        if (!query) return;

        window.appendMessage("user", query);
        userInput.value = "";
        sendBtn.disabled = true;

        const loadingId = "loading-msg";
        const loadingDiv = document.createElement("div");
        loadingDiv.id = loadingId;
        loadingDiv.className = "text-muted small mb-2 ms-2";
        loadingDiv.textContent = "AI is typing...";
        chatBox.appendChild(loadingDiv);
        chatBox.scrollTop = chatBox.scrollHeight;

        try {
            const res = await fetch(chatUrl, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message: query })
            });

            const data = await res.json();
            document.getElementById(loadingId)?.remove();

            if (res.ok) {
                window.appendMessage("ai", data.response);
            } else {
                window.appendMessage("ai", "Error: " + (data.error || "Failed to fetch response."));
            }
        } catch (err) {
            document.getElementById(loadingId)?.remove();
            window.appendMessage("ai", "Network error: Unable to reach the server.");
        } finally {
            sendBtn.disabled = false;
            userInput.focus();
        }
    });
});