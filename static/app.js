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
        const currentRole = ref('');
        const loginForm = ref({ username: '', password: '' });
        const loginError = ref('');
        const loginLoading = ref(false);

        const STORAGE_KEY = 'ai-chat-conversations';

        // --- Persistence ---

        const persistConversations = () => {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations.value));
        };

        // --- Server API helpers (fire-and-forget, swallow errors) ---

        const apiSaveConversation = async (conv) => {
            try {
                await fetch(`/conversations/${conv.id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        title: conv.title,
                        messages: conv.messages,
                        created_at: conv.createdAt
                    })
                });
            } catch (e) { console.error('apiSaveConversation', e); }
        };

        const apiDeleteConversation = async (id) => {
            try {
                await fetch(`/conversations/${id}`, { method: 'DELETE' });
            } catch (e) { console.error('apiDeleteConversation', e); }
        };

        const apiLoadConversations = async () => {
            try {
                const res = await fetch('/conversations');
                if (!res.ok) return null;
                return await res.json();
            } catch (e) { console.error('apiLoadConversations', e); return null; }
        };

        const apiLoadConversationMessages = async (id) => {
            try {
                const res = await fetch(`/conversations/${id}`);
                if (!res.ok) return null;
                return await res.json();
            } catch (e) { console.error('apiLoadConversationMessages', e); return null; }
        };

        const loadConversationsFromServer = async () => {
            const serverConvs = await apiLoadConversations();
            if (serverConvs && serverConvs.length > 0) {
                conversations.value = serverConvs.map(c => ({
                    id: c.id,
                    title: c.title,
                    messages: [],
                    createdAt: new Date(c.created_at).getTime(),
                    _messagesLoaded: false
                }));
                persistConversations();
            } else {
                // Server is empty — check localStorage for migration
                let local = [];
                try {
                    const raw = localStorage.getItem(STORAGE_KEY);
                    if (raw) local = JSON.parse(raw);
                } catch (_) {}
                if (local.length > 0) {
                    const migrate = confirm(`You have ${local.length} conversation(s) saved locally. Migrate them to the server?`);
                    if (migrate) {
                        conversations.value = local;
                        for (const conv of local) {
                            await apiSaveConversation(conv);
                        }
                        persistConversations();
                    }
                }
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
            const conv = conversations.value.find(c => c.id === currentConvId.value);
            if (conv) apiSaveConversation(conv);
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

        const loadConversation = async (id) => {
            saveCurrentChat();
            const conv = conversations.value.find(c => c.id === id);
            if (!conv) return;
            if (!conv._messagesLoaded && conv.messages.length === 0) {
                const full = await apiLoadConversationMessages(id);
                if (full) { conv.messages = full.messages; conv._messagesLoaded = true; }
            }
            messages.value = [...conv.messages];
            currentConvId.value = id;
            lastResponseMs.value = null;
            if (window.innerWidth < 768) sidebarOpen.value = false;
            nextTick(() => scrollToBottom());
        };

        const deleteConversation = (id) => {
            apiDeleteConversation(id);
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
                currentRole.value = data.role || '';
                loginForm.value = { username: '', password: '' };
                await loadConversationsFromServer();
                currentConvId.value = generateId();
            } finally {
                loginLoading.value = false;
            }
        };

        const logout = async () => {
            await fetch('/auth/logout', { method: 'POST' });
            isAuthenticated.value = false;
            currentUser.value = '';
            currentRole.value = '';
            messages.value = [];
            conversations.value = [];
            loginError.value = '';
            localStorage.removeItem(STORAGE_KEY);
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
                    currentRole.value = data.role || '';
                    await loadConversationsFromServer();
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
            isAuthenticated, currentUser, currentRole, loginForm, loginError, loginLoading,
            sendMessage, renderMarkdown, autoResize,
            newChat, loadConversation, deleteConversation, formatDate,
            login, logout
        };
    }
}).mount('#app');
