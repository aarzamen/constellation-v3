/**
 * Search interface for Constellation V3.
 * Client-side keyword filtering + server-side semantic search.
 */

let searchTimeout = null;

function initSearch() {
    const input = document.getElementById('search-input');
    if (!input) return;

    input.addEventListener('input', () => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
            performSearch(input.value.trim());
        }, 300);
    });

    input.addEventListener('keydown', e => {
        if (e.key === 'Escape') {
            input.value = '';
            clearSearch();
        }
    });
}

function performSearch(query) {
    if (!query) {
        clearSearch();
        return;
    }

    // Try client-side keyword match first
    const lq = query.toLowerCase();
    const keywordMatches = graphData.nodes.filter(n =>
        n.name.toLowerCase().includes(lq) ||
        (n.topTerms && n.topTerms.some(t => t.includes(lq))) ||
        (n.snippet && n.snippet.toLowerCase().includes(lq))
    );

    if (keywordMatches.length > 0) {
        showSearchResults(keywordMatches.slice(0, 10), 'keyword');
        highlightNodes(keywordMatches.map(n => n.id));
        return;
    }

    // Fall back to semantic search
    semanticSearch(query);
}

async function semanticSearch(query) {
    const resultsDiv = document.getElementById('search-results');
    resultsDiv.innerHTML = '<div class="search-result" style="color:var(--text-dim)">Searching...</div>';

    try {
        const response = await fetch('/api/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query, top_k: 10 })
        });
        const results = await response.json();

        if (results.length === 0) {
            resultsDiv.innerHTML = '<div class="search-result" style="color:var(--text-dim)">No results</div>';
            return;
        }

        // Map results to nodes
        const resultNodes = results.map(r => {
            const node = graphData.nodes.find(n => n.id === r.id);
            return node || { id: r.id, name: r.title, date: r.date, score: r.score };
        }).filter(Boolean);

        showSearchResults(resultNodes, 'hybrid');
        highlightNodes(resultNodes.map(n => n.id));
    } catch (err) {
        resultsDiv.innerHTML = '<div class="search-result" style="color:var(--text-dim)">Search error</div>';
    }
}

function showSearchResults(nodes, type) {
    const resultsDiv = document.getElementById('search-results');
    const label = type === 'hybrid' ? ' (hybrid)' : ' (keyword)';

    resultsDiv.innerHTML = nodes.map(n => `
        <div class="search-result" onclick="focusNode('${n.id}', document.getElementById('search-input').value)">
            <strong>${escapeHtml(n.name)}</strong>${label}
            <br><small>${n.date || ''}${n.score ? ' \u00b7 Semantic: ' + (n.score * 100).toFixed(0) + '% | RRF: ' + (n.rrf_score ? n.rrf_score.toFixed(3) : '') : ''}</small>
        </div>
    `).join('');
}

function focusNode(nodeId, query = '') {
    const node = graphData.nodes.find(n => n.id === nodeId);
    if (node) {
        if (filteredClusters.has(node.cluster)) {
            filteredClusters.delete(node.cluster);
            buildClusterList();
            applyFilters();
        }
        handleNodeClick(node, query);
    }
}

function clearSearch() {
    const resultsDiv = document.getElementById('search-results');
    if (resultsDiv) resultsDiv.innerHTML = '';
    clearHighlight();
}
