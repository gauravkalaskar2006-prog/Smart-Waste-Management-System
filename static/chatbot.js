document.addEventListener("DOMContentLoaded", () => {
    const chatbotToggler = document.querySelector(".chatbot-toggler");
    const closeBtn = document.querySelector(".close-btn");
    const chatbox = document.querySelector(".chatbox");
    const chatInput = document.querySelector(".chat-input input");
    const sendChatBtn = document.querySelector(".chat-input button");

    let userMessage;

    // Toggle chatbot
    chatbotToggler.addEventListener("click", () => document.body.classList.toggle("show-chatbot"));
    closeBtn.addEventListener("click", () => document.body.classList.remove("show-chatbot"));

    const createChatLi = (message, className, isHTML = false) => {
        const chatLi = document.createElement("li");
        chatLi.classList.add("chat", className);
        let chatContent = className === "outgoing" ? `<p></p>` : `<p></p>`;
        chatLi.innerHTML = chatContent;
        if(isHTML) {
            chatLi.querySelector("p").innerHTML = message;
        } else {
            chatLi.querySelector("p").textContent = message;
        }
        return chatLi;
    };

    const generateResponse = async (incomingChatLi) => {
        const messageElement = incomingChatLi.querySelector("p");

        try {
            const response = await fetch("/chatbot", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({ message: userMessage })
            });

            const data = await response.json();
            messageElement.innerHTML = data.reply || data.response;

            // Render suggestions if they exist
            if (data.suggestions && data.suggestions.length > 0) {
                const suggestionDiv = document.createElement("div");
                suggestionDiv.classList.add("chat-suggestions");
                
                data.suggestions.forEach(sugg => {
                    const btn = document.createElement("button");
                    btn.classList.add("suggestion-btn");
                    btn.textContent = sugg;
                    btn.onclick = () => {
                        chatInput.value = sugg.replace(/[\uD800-\uDBFF][\uDC00-\uDFFF]\s?/g, ''); // remove emoji for cleaner input processing
                        handleChat();
                        suggestionDiv.remove(); // Remove suggestions after clicking one
                    };
                    suggestionDiv.appendChild(btn);
                });
                
                // Append suggestions outside the chat bubble but inside the list
                incomingChatLi.appendChild(suggestionDiv);
                
                // Also adjust bubble border radius since there are items beneath
                messageElement.style.borderRadius = "0 12px 0px 12px";
            }

        } catch (error) {
            messageElement.textContent = "Oops! Something went wrong. Please try again.";
        } finally {
            chatbox.scrollTo(0, chatbox.scrollHeight);
        }
    };

    const handleChat = () => {
        userMessage = chatInput.value.trim();
        if (!userMessage) return;

        chatInput.value = "";
        
        // Append user's message to chatbox
        chatbox.appendChild(createChatLi(userMessage, "outgoing"));
        chatbox.scrollTo(0, chatbox.scrollHeight);

        setTimeout(() => {
            // Show typing indicator
            const incomingChatLi = createChatLi(`<div class="typing-indicator"><span></span><span></span><span></span></div>`, "incoming", true);
            chatbox.appendChild(incomingChatLi);
            chatbox.scrollTo(0, chatbox.scrollHeight);
            
            // fetch response
            generateResponse(incomingChatLi);
        }, 600);
    };

    sendChatBtn.addEventListener("click", handleChat);
    chatInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            handleChat();
        }
    });

    // Initial greeting if not already present
    setTimeout(() => {
        if(chatbox.children.length === 0) {
            // First we trigger the backend with a greeting prompt to get the standard reply + buttons
            userMessage = "hello";
            const incomingChatLi = createChatLi(`<div class="typing-indicator"><span></span><span></span><span></span></div>`, "incoming", true);
            chatbox.appendChild(incomingChatLi);
            generateResponse(incomingChatLi);
        }
    }, 500);

});
