(() => {
    'use strict';

    const root = document.querySelector('[data-fixnow-ai]');
    if (!root) return;

    const launcher = root.querySelector('[data-ai-launcher]');
    const panel = root.querySelector('[data-ai-panel]');
    const closeButton = root.querySelector('[data-ai-close]');
    const form = root.querySelector('[data-ai-form]');
    const input = root.querySelector('[data-ai-input]');
    const sendButton = root.querySelector('[data-ai-send]');
    const messages = root.querySelector('[data-ai-messages]');
    const emptyState = root.querySelector('[data-ai-empty]');
    const status = root.querySelector('[data-ai-status]');
    const csrfToken = root.querySelector('input[name="csrfmiddlewaretoken"]')?.value || '';
    const endpoint = root.dataset.chatEndpoint;
    const requestTimeoutMs = 65000;
    const focusableSelector = 'button:not([disabled]), textarea:not([disabled]), input:not([disabled]), [href]';

    let isOpen = false;
    let isSending = false;
    let lastMessage = '';
    let activeController = null;
    const conversationHistory = [];

    function setOpen(nextOpen) {
        isOpen = nextOpen;
        panel.hidden = !isOpen;
        launcher.setAttribute('aria-expanded', String(isOpen));
        launcher.setAttribute('aria-label', isOpen ? 'Close FixNow AI assistant' : 'Open FixNow AI assistant');

        if (isOpen) {
            window.requestAnimationFrame(() => input.focus());
        } else {
            launcher.focus();
        }
    }

    function setStatus(message) {
        status.textContent = message || '';
    }

    function scrollToLatest() {
        messages.scrollTop = messages.scrollHeight;
    }

    function appendMessage(role, text, state) {
        emptyState.hidden = true;
        const message = document.createElement('div');
        message.className = 'fixnow-ai-message';
        message.dataset.role = role;
        if (state) message.dataset.state = state;
        message.textContent = text;
        messages.appendChild(message);
        scrollToLatest();
        return message;
    }

    function appendLoadingMessage() {
        emptyState.hidden = true;
        const message = document.createElement('div');
        message.className = 'fixnow-ai-message';
        message.dataset.role = 'assistant';
        message.dataset.state = 'loading';
        message.setAttribute('aria-label', 'FixNow AI is thinking');
        for (let i = 0; i < 3; i += 1) {
            const dot = document.createElement('span');
            dot.className = 'fixnow-ai-dot';
            dot.setAttribute('aria-hidden', 'true');
            message.appendChild(dot);
        }
        messages.appendChild(message);
        scrollToLatest();
        return message;
    }

    function appendErrorMessage(messageText) {
        const message = appendMessage('assistant', messageText, 'error');
        const retry = document.createElement('button');
        retry.type = 'button';
        retry.className = 'fixnow-ai-retry';
        retry.textContent = 'Try again';
        retry.addEventListener('click', () => {
            if (lastMessage && !isSending) sendMessage(lastMessage);
        });
        message.appendChild(retry);
        scrollToLatest();
    }

    function appendBookingLink(url) {
        if (typeof url !== 'string' || !url.startsWith('/')) return;
        let parsed;
        try {
            parsed = new URL(url, window.location.origin);
        } catch (error) {
            return;
        }
        if (parsed.origin !== window.location.origin || !/^\/book\/\d+\/?$/.test(parsed.pathname)) return;

        const action = document.createElement('a');
        action.className = 'fixnow-ai-booking-link';
        action.href = `${parsed.pathname}${parsed.search}`;
        action.textContent = 'Continue to booking';
        action.setAttribute('aria-label', 'Continue to the FixNow booking form');
        messages.appendChild(action);
        scrollToLatest();
    }

    function readableField(field) {
        return String(field)
            .replaceAll('_', ' ')
            .replace(/\b\w/g, (letter) => letter.toUpperCase());
    }

    function responseText(payload) {
        if (typeof payload.assistant_message === 'string' && payload.assistant_message.trim()) {
            return payload.assistant_message.trim();
        }
        if (payload.is_hazard) {
            const guidance = typeof payload.safety_guidance === 'string' ? payload.safety_guidance : 'Please move to a safe place and contact emergency services.';
            const contacts = Array.isArray(payload.emergency_contacts) && payload.emergency_contacts.length
                ? `\n\nEmergency contacts: ${payload.emergency_contacts.join(', ')}`
                : '';
            return `${guidance}${contacts}`;
        }

        const parts = [];
        if (typeof payload.problem_summary === 'string' && payload.problem_summary.trim()) {
            parts.push(`I understood: ${payload.problem_summary.trim()}`);
        }
        if (payload.intent === 'unsupported_or_out_of_scope') {
            parts.push('That request is outside the home services currently supported by FixNow.');
        }
        if (payload.needs_clarification && Array.isArray(payload.missing_fields) && payload.missing_fields.length) {
            parts.push(`To continue, I still need: ${payload.missing_fields.map(readableField).join(', ')}.`);
        }
        if (!parts.length) {
            parts.push('I need a little more detail to help with that request.');
        }
        return parts.join('\n\n');
    }

    async function readResponse(response) {
        const raw = await response.text();
        let payload;
        try {
            payload = raw ? JSON.parse(raw) : null;
        } catch (error) {
            throw new Error('The assistant returned an unexpected response.');
        }
        if (!response.ok) {
            if (response.status === 429) throw new Error('The assistant is busy. Please wait a moment and try again.');
            if (response.status === 503) throw new Error('The assistant is temporarily unavailable.');
            throw new Error(payload && typeof payload.error === 'string' ? payload.error : 'The assistant could not process that request.');
        }
        if (!payload || payload.ok !== true) {
            throw new Error('The assistant could not process that request.');
        }
        return payload;
    }

    async function sendMessage(message) {
        if (isSending || !message) return;
        isSending = true;
        lastMessage = message;
        input.value = '';
        input.style.height = '';
        setStatus('');
        appendMessage('user', message);
        const loadingMessage = appendLoadingMessage();
        sendButton.disabled = true;
        input.disabled = true;
        activeController = new AbortController();
        const timeout = window.setTimeout(() => activeController.abort(), requestTimeoutMs);

        try {
            const response = await fetch(endpoint, {
                method: 'POST',
                credentials: 'same-origin',
                signal: activeController.signal,
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken,
                    'X-Requested-With': 'XMLHttpRequest',
                },
                body: JSON.stringify({
                    message,
                    history: conversationHistory.slice(-6),
                }),
            });
            const payload = await readResponse(response);
            loadingMessage.remove();
            appendMessage('assistant', responseText(payload));
            appendBookingLink(payload.booking_url);
            conversationHistory.push({ role: 'user', content: message });
            conversationHistory.push({
                role: 'assistant',
                content: responseText(payload),
                context: {
                    intent: payload.intent,
                    category_slug: payload.category_slug,
                    pincode: payload.pincode,
                    problem_summary: payload.problem_summary,
                    pending_action: payload.pending_action,
                },
            });
        } catch (error) {
            loadingMessage.remove();
            if (error.name === 'AbortError') {
                appendErrorMessage('The assistant took too long to respond.');
            } else if (error instanceof TypeError) {
                appendErrorMessage('The assistant could not be reached. Check your connection and try again.');
            } else {
                appendErrorMessage(error.message || 'The assistant is temporarily unavailable.');
            }
        } finally {
            window.clearTimeout(timeout);
            activeController = null;
            isSending = false;
            sendButton.disabled = false;
            input.disabled = false;
            input.focus();
        }
    }

    launcher.addEventListener('click', () => setOpen(!isOpen));
    closeButton.addEventListener('click', () => setOpen(false));

    form.addEventListener('submit', (event) => {
        event.preventDefault();
        const message = input.value.trim();
        if (message) sendMessage(message);
    });

    input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            form.requestSubmit();
        }
    });

    input.addEventListener('input', () => {
        input.style.height = '';
        input.style.height = `${Math.min(input.scrollHeight, 100)}px`;
    });

    panel.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            setOpen(false);
            return;
        }
        if (event.key !== 'Tab') return;
        const focusable = [...panel.querySelectorAll(focusableSelector)];
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    });
})();
