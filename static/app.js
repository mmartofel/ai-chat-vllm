const { createApp, ref, computed, nextTick, onMounted } = Vue;

// Configure marked: GFM, soft line-breaks, and inline hljs highlighting
marked.use({
    gfm: true,
    breaks: true,
    renderer: {
        code({ text, lang }) {
            const validLang = lang && hljs.getLanguage(lang) ? lang : null;
            let highlighted;
            try {
                highlighted = validLang
                    ? hljs.highlight(text, { language: validLang, ignoreIllegals: true }).value
                    : hljs.highlightAuto(text).value;
            } catch (_) {
                highlighted = text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
            }
            const langLabel = validLang
                ? `<span class="code-lang-label">${validLang}</span>`
                : '';
            return `<div class="code-block"><div class="code-header">${langLabel}</div><pre><code class="hljs">${highlighted}</code></pre></div>`;
        }
    }
});

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
        const MAX_CTX_MESSAGES = 20;
        const useRag = ref(true);

        const estimatedTokens = computed(() =>
            Math.round(messages.value.reduce((sum, m) => sum + (m.content?.length || 0), 0) / 4)
        );

        // Auth state
        const isImageLoading = ref(false);
        const imageUploadRef = ref(null);

        // RAG / documents
        const documents    = ref([]);
        const isIndexing   = ref(false);
        const docUploadRef = ref(null);

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

        const clearContext = () => {
            messages.value = [];
            status.value = 'Context cleared';
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

        const renderMarkdown = (text, cursor = false) => {
            if (!text) return '';
            const html = marked.parse(text);
            const safe = typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(html) : html;
            return cursor ? safe + '<span class="typing-cursor"></span>' : safe;
        };

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

        // Add "Copy" buttons to code block headers rendered in the chat
        const addCopyButtons = () => {
            document.querySelectorAll('.code-block:not([data-copy-added])').forEach(block => {
                block.setAttribute('data-copy-added', 'true');
                const header = block.querySelector('.code-header');
                const code = block.querySelector('code');
                if (!header || !code) return;
                const btn = document.createElement('button');
                btn.textContent = 'Copy';
                btn.className = 'copy-btn';
                btn.onclick = () => {
                    navigator.clipboard.writeText(code.innerText ?? '').then(() => {
                        btn.textContent = 'Copied!';
                        setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
                    });
                };
                header.appendChild(btn);
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
                await loadDocuments();
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

        // --- Image functions ---

        const sendImage = async (file) => {
            if (isStreaming.value || isImageLoading.value) return;

            const objectUrl = URL.createObjectURL(file);
            const userMsg = {
                role: 'user',
                type: 'image',
                url: objectUrl,
                content: `[Image: ${file.name}]`,
                durationMs: null,
            };
            messages.value.push(userMsg);
            await nextTick();
            scrollToBottom();

            isImageLoading.value = true;
            status.value = 'Analysing image...';

            const formData = new FormData();
            formData.append('file', file);
            formData.append('conversation_id', currentConvId.value);

            let imageMinioUrl = null;
            try {
                const res = await fetch('/images/describe', {
                    method: 'POST',
                    body: formData,
                });
                if (res.status === 401) { isAuthenticated.value = false; return; }
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    throw new Error(err.detail || `HTTP ${res.status}`);
                }
                const data = await res.json();
                imageMinioUrl = data.image_url ?? null;
                messages.value.push({
                    role: 'assistant',
                    type: 'text',
                    content: data.result_text || '[No description returned from vision model]',
                    durationMs: null,
                });
            } catch (e) {
                messages.value.push({
                    role: 'assistant',
                    type: 'text',
                    content: `[Image analysis failed: ${e.message}]`,
                    durationMs: null,
                });
            } finally {
                isImageLoading.value = false;
                status.value = 'Ready';
                // Replace transient blob URL with permanent MinIO URL
                userMsg.url = imageMinioUrl;
                saveCurrentChat();
                await nextTick();
                scrollToBottom();
            }
        };

        const generateImage = async (prompt) => {
            messages.value.push({
                role: 'user', type: 'text',
                content: `/image ${prompt}`, durationMs: null,
            });
            isImageLoading.value = true;
            status.value = 'Generating image…';
            await nextTick();
            scrollToBottom();

            const prevGenMsg = [...messages.value].reverse()
                .find(m => m.role === 'assistant' && m.type === 'image' && m.url);
            const previousImageUrl = prevGenMsg?.url ?? null;

            try {
                const res = await fetch('/images/generate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        conversation_id: currentConvId.value,
                        prompt,
                        width: 1024,
                        height: 576,
                        previous_image_url: previousImageUrl,
                        use_rag: useRag.value,
                    }),
                });
                if (res.status === 401) { isAuthenticated.value = false; return; }
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    throw new Error(err.detail || `HTTP ${res.status}`);
                }
                const data = await res.json();
                messages.value.push({
                    role: 'assistant', type: 'image',
                    url: data.image_url, content: prompt, durationMs: null,
                });
            } catch (e) {
                messages.value.push({
                    role: 'assistant', type: 'text',
                    content: `[Image generation failed: ${e.message}]`, durationMs: null,
                });
            } finally {
                isImageLoading.value = false;
                status.value = 'Ready';
                saveCurrentChat();
                await nextTick();
                scrollToBottom();
            }
        };

        // --- Send message ---

        const sendMessage = async () => {
            if (!userInput.value.trim() || isStreaming.value || isImageLoading.value) return;

            if (userInput.value.trim().startsWith('/image ')) {
                const prompt = userInput.value.trim().slice(7).trim();
                if (!prompt) return;
                resetTextarea();
                await generateImage(prompt);
                return;
            }

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
                    body: JSON.stringify({ messages: messages.value.slice(0, -1).slice(-MAX_CTX_MESSAGES), use_rag: useRag.value })
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
                    assistantMsg.content += decoder.decode(value);
                    await nextTick();
                    scrollToBottom();
                }

                // Extract %%SOURCES%% footer appended by the RAG pipeline
                const marker = '\n%%SOURCES%%';
                const idx = assistantMsg.content.indexOf(marker);
                if (idx !== -1) {
                    try {
                        assistantMsg.sources = JSON.parse(assistantMsg.content.slice(idx + marker.length));
                    } catch (_) {}
                    assistantMsg.content = assistantMsg.content.slice(0, idx);
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
                setTimeout(() => { addCopyButtons(); }, 50);
                textareaRef.value?.focus();
                await nextTick();
                scrollToBottom();
            }
        };

        // --- Documents ---

        const loadDocuments = async () => {
            try {
                const res = await fetch('/documents');
                if (res.ok) documents.value = await res.json();
            } catch (_) {}
        };

        const uploadDocument = async (file) => {
            isIndexing.value = true;
            status.value = `Indexing ${file.name}…`;
            try {
                const fd = new FormData();
                fd.append('file', file);
                const res = await fetch('/documents/upload', { method: 'POST', body: fd });
                if (res.status === 401) { isAuthenticated.value = false; return; }
                if (!res.ok) {
                    const body = await res.json().catch(() => ({}));
                    status.value = `Index failed: ${body.detail || `HTTP ${res.status}`}`;
                } else {
                    status.value = 'Ready';
                }
                await loadDocuments();
            } catch (e) {
                status.value = `Index failed: ${e.message}`;
            } finally {
                isIndexing.value = false;
            }
        };

        const deleteDocument = async (id) => {
            try {
                await fetch(`/documents/${id}`, { method: 'DELETE' });
                documents.value = documents.value.filter(d => d.id !== id);
            } catch (_) {}
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
                    await loadDocuments();
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
            isImageLoading, imageUploadRef, sendImage, generateImage,
            documents, isIndexing, docUploadRef, loadDocuments, uploadDocument, deleteDocument,
            sendMessage, renderMarkdown, autoResize,
            newChat, clearContext, loadConversation, deleteConversation, formatDate,
            useRag, estimatedTokens,
            login, logout
        };
    }
}).mount('#app');
