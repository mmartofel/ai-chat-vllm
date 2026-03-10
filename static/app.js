const { createApp, ref, nextTick, onMounted } = Vue;

createApp({
    setup() {
        const userInput = ref('');
        const messages = ref([]);
        const isStreaming = ref(false);
        const status = ref('Ready');
        const textareaRef = ref(null);

        // Sidebar & conversation history
        const sidebarOpen = ref(window.innerWidth >= 768);
        const conversations = ref([]);
        const currentConvId = ref(null);
        const serverInfo = ref({ model: '', url: '' });
        const lastResponseMs = ref(null);

        // Auth state
        const isAuthenticated = ref(false);
        const currentUser = ref('');
        const loginForm = ref({ username: '', password: '' });
        const loginError = ref('');
        const loginLoading = ref(false);

        const STORAGE_KEY = 'ai-chat-conversations';

        // --- Persistence ---

        const persistConversations = () => {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations.value));
        };

        const loadFromStorage = () => {
            try {
                const raw = localStorage.getItem(STORAGE_KEY);
                if (raw) conversations.value = JSON.parse(raw);
            } catch (_) {
                conversations.value = [];
            }
        };

        const generateId = () => Math.random().toString(36).slice(2) + Date.now().toString(36);

        // Save the active chat into the conversations list (upsert by id)
        const saveCurrentChat = () => {
            if (messages.value.length === 0) return;
            const title = messages.value.find(m => m.role === 'user')?.content?.slice(0, 60) || 'Conversation';
            const existing = conversations.value.find(c => c.id === currentConvId.value);
            if (existing) {
                existing.messages = [...messages.value];
                existing.title = title;
            } else {
                conversations.value.unshift({
                    id: currentConvId.value,
                    title,
                    messages: [...messages.value],
                    createdAt: Date.now()
                });
            }
            persistConversations();
        };

        // --- Conversation actions ---

        const newChat = () => {
            saveCurrentChat();
            messages.value = [];
            currentConvId.value = generateId();
            lastResponseMs.value = null;
            if (window.innerWidth < 768) sidebarOpen.value = false;
            nextTick(() => textareaRef.value?.focus());
        };

        const loadConversation = (id) => {
            saveCurrentChat();
            const conv = conversations.value.find(c => c.id === id);
            if (!conv) return;
            messages.value = [...conv.messages];
            currentConvId.value = id;
            lastResponseMs.value = null;
            if (window.innerWidth < 768) sidebarOpen.value = false;
            nextTick(() => scrollToBottom());
        };

        const deleteConversation = (id) => {
            conversations.value = conversations.value.filter(c => c.id !== id);
            persistConversations();
            if (currentConvId.value === id) {
                messages.value = [];
                currentConvId.value = generateId();
                lastResponseMs.value = null;
            }
        };

        const formatDate = (ts) => {
            const d = new Date(ts);
            const now = new Date();
            if (d.toDateString() === now.toDateString()) return 'Today';
            const yesterday = new Date(now);
            yesterday.setDate(now.getDate() - 1);
            if (d.toDateString() === yesterday.toDateString()) return 'Yesterday';
            return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
        };

        // --- UI helpers ---

        const renderMarkdown = (text) => marked.parse(text);

        const scrollToBottom = () => {
            const container = document.getElementById('chat-container');
            if (container) container.scrollTop = container.scrollHeight;
        };

        const autoResize = () => {
            const el = textareaRef.value;
            if (el) {
                el.style.height = 'auto';
                el.style.height = el.scrollHeight + 'px';
            }
        };

        const resetTextarea = () => {
            userInput.value = '';
            if (textareaRef.value) textareaRef.value.style.height = 'auto';
        };

        // Add "Copy" buttons to code blocks rendered in the chat
        const addCopyButtons = () => {
            document.querySelectorAll('pre:not([data-copy-added])').forEach(pre => {
                pre.setAttribute('data-copy-added', 'true');
                const btn = document.createElement('button');
                btn.textContent = 'Copy';
                btn.className = 'copy-btn';
                btn.onclick = () => {
                    const code = pre.querySelector('code')?.innerText ?? pre.innerText;
                    navigator.clipboard.writeText(code).then(() => {
                        btn.textContent = 'Copied!';
                        setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
                    });
                };
                pre.appendChild(btn);
            });
        };

        // --- Auth ---

        const login = async () => {
            loginError.value = '';
            loginLoading.value = true;
            try {
                const res = await fetch('/auth/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(loginForm.value)
                });
                if (!res.ok) {
                    const data = await res.json().catch(() => ({}));
                    loginError.value = data.detail || 'Invalid credentials';
                    return;
                }
                const data = await res.json();
                isAuthenticated.value = true;
                currentUser.value = data.username;
                loginForm.value = { username: '', password: '' };
                loadFromStorage();
                currentConvId.value = generateId();
            } finally {
                loginLoading.value = false;
            }
        };

        const logout = async () => {
            await fetch('/auth/logout', { method: 'POST' });
            isAuthenticated.value = false;
            currentUser.value = '';
            messages.value = [];
            conversations.value = [];
            loginError.value = '';
        };

        // --- Send message ---

        const sendMessage = async () => {
            if (!userInput.value.trim() || isStreaming.value) return;

            messages.value.push({ role: 'user', content: userInput.value });
            resetTextarea();
            isStreaming.value = true;
            status.value = 'Thinking...';
            await nextTick();
            scrollToBottom();

            const assistantMsg = { role: 'assistant', content: '', durationMs: null };
            messages.value.push(assistantMsg);
            const startTime = Date.now();

            try {
                const response = await fetch('/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ messages: messages.value.slice(0, -1) })
                });

                if (response.status === 401) {
                    isAuthenticated.value = false;
                    return;
                }

                if (!response.ok) throw new Error(`HTTP ${response.status}`);

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                status.value = 'Streaming...';

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    messages.value[messages.value.length - 1].content += decoder.decode(value);
                    await nextTick();
                    scrollToBottom();
                }
            } catch (error) {
                messages.value[messages.value.length - 1].content +=
                    '\n\n[Connection error — is the model server running?]';
            } finally {
                const elapsed = Date.now() - startTime;
                messages.value[messages.value.length - 1].durationMs = elapsed;
                lastResponseMs.value = elapsed;
                isStreaming.value = false;
                status.value = 'Ready';
                saveCurrentChat();
                setTimeout(() => { hljs.highlightAll(); addCopyButtons(); }, 100);
                textareaRef.value?.focus();
            }
        };

        // --- Init ---

        onMounted(async () => {
            try {
                const res = await fetch('/auth/me');
                if (res.ok) {
                    const data = await res.json();
                    isAuthenticated.value = true;
                    currentUser.value = data.username;
                    loadFromStorage();
                    currentConvId.value = generateId();
                }
            } catch (_) {}

            try {
                const res = await fetch('/health');
                const data = await res.json();
                serverInfo.value = { model: data.model ?? '', url: data.target ?? '' };
            } catch (_) {}
        });

        return {
            userInput, messages, isStreaming, status, textareaRef,
            sidebarOpen, conversations, currentConvId, serverInfo, lastResponseMs,
            isAuthenticated, currentUser, loginForm, loginError, loginLoading,
            sendMessage, renderMarkdown, autoResize,
            newChat, loadConversation, deleteConversation, formatDate,
            login, logout
        };
    }
}).mount('#app');
