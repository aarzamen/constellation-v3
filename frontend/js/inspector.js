/**
 * Inspector panel for Constellation V3.
 * Shows conversation detail and chat viewer on node click.
 */

let currentInspectorNode = null;
let loadedMessages = 10;

function showInspector(node, query = '') {
    currentInspectorNode = node;
    loadedMessages = 10;
    const app = document.getElementById('app');
    app.classList.add('inspector-open');

    const inspector = document.getElementById('inspector');

    // Fetch full conversation
    fetch(`/api/conversation/${node.id}`)
        .then(r => r.json())
        .then(conv => {
            renderInspector(node, conv, query);
        })
        .catch(() => {
            renderInspector(node, null, query);
        });
}

function hideInspector() {
    const app = document.getElementById('app');
    app.classList.remove('inspector-open');
    currentInspectorNode = null;

    // Resize graph
    if (Graph) {
        setTimeout(() => {
            Graph.width(document.getElementById('graph-viewport').offsetWidth);
        }, 300);
    }
}

function renderInspector(node, conv) {
    const inspector = document.getElementById('inspector');
    const messages = conv && conv.messages ? conv.messages : [];

    let termsHtml = '';
    if (node.topTerms && node.topTerms.length > 0) {
        termsHtml = `<div class="inspector-terms">
            ${node.topTerms.map(t => `<span class="term-badge">${escapeHtml(t)}</span>`).join('')}
        </div>`;
    }

    let messagesHtml = '';
    const displayMessages = messages.slice(0, loadedMessages);
    displayMessages.forEach(m => {
        const roleClass = m.role === 'user' ? 'user' : 'assistant';
        const textClass = m.role === 'user' ? 'user-text' : '';
        const text = m.text.length > 2000 ? m.text.substring(0, 2000) + '...' : m.text;
        const escapedText = escapeHtml(text);
        const highlightedText = highlightQuery(escapedText, query);
        messagesHtml += `
            <div class="chat-message">
                <div class="chat-role ${roleClass}">${m.role}</div>
                <div class="chat-text ${textClass}">${highlightedText}</div>
            </div>`;
    });

    if (messages.length > loadedMessages) {
        messagesHtml += `
            <div class="chat-more">
                <button onclick="loadMoreMessages()">
                    Show more (${messages.length - loadedMessages} remaining)
                </button>
            </div>`;
    }

    inspector.innerHTML = `
        <div class="inspector-header">
            <h2>${escapeHtml(node.name)}</h2>
            <button class="inspector-close" onclick="hideInspector()">\u00d7</button>
        </div>
        <div class="inspector-meta">
            <div class="meta-item">
                <div class="meta-label">Provider</div>
                <div class="meta-value" style="color: ${node.provider === 'chatgpt' ? '#10a37f' : node.provider === 'gemini' ? '#d9a857' : node.provider === 'grok' ? '#d97957' : '#7957d9'}">
                    ${node.provider === 'chatgpt' ? 'ChatGPT' : node.provider === 'gemini' ? 'Gemini' : node.provider === 'grok' ? 'Grok' : 'Claude'}
                </div>
            </div>
            <div class="meta-item">
                <div class="meta-label">Date</div>
                <div class="meta-value">${node.date}</div>
            </div>
            <div class="meta-item">
                <div class="meta-label">Messages</div>
                <div class="meta-value">${node.messageCount}</div>
            </div>
            <div class="meta-item">
                <div class="meta-label">Cluster</div>
                <div class="meta-value">
                    <span class="cluster-dot" style="display:inline-block;background:${node.color}"></span>
                    ${escapeHtml(node.clusterLabel)}
                </div>
            </div>
        </div>
        ${termsHtml}
        <div class="chat-viewer">
            ${messagesHtml || '<div style="color:var(--text-dim);padding:20px;text-align:center;">No messages available</div>'}
        </div>`;

    // Store messages for load more
    inspector._messages = messages;
}

function loadMoreMessages() {
    loadedMessages += 20;
    if (currentInspectorNode) {
        const inspector = document.getElementById('inspector');
        const messages = inspector._messages || [];
        renderInspector(currentInspectorNode, { messages });
    }
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function highlightQuery(escapedText, query) {
    if (!query) return escapedText;
    const terms = query.trim().split(/\s+/).filter(t => t.length > 2);
    if (terms.length === 0) return escapedText;

    let highlighted = escapedText;
    terms.forEach(term => {
        const safeTerm = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        const regex = new RegExp(`(${safeTerm})`, 'gi');
        highlighted = highlighted.replace(regex, '<mark style="background: rgba(121, 87, 217, 0.4); color: inherit; padding: 0 4px; border-radius: 3px; font-weight: bold;">$1</mark>');
    });
    return highlighted;
}
