/**
 * Main orchestrator for Constellation.
 * Loads data, initializes components, wires events.
 */

let appData = null;

async function init() {
    try {
        // Load graph data
        const response = await fetch(`/data/graph_data.json?t=${Date.now()}`, { cache: 'no-store' });
        appData = await response.json();

        // Store all edges for percentile filtering
        appData._allEdges = [...appData.edges];

        // Initialize components
        initStarfield();
        initGraph(appData);
        initTimeline(appData.timeline, appData.clusters);
        initSearch();
        buildProviderList();
        buildClusterList();
        buildControls();
        updateTopBar(appData.stats);
        updateStatusBar(appData.nodes.length);

        // Hide loading screen
        setTimeout(() => {
            document.getElementById('loading-screen').classList.add('hidden');
        }, 500);

    } catch (err) {
        console.error('Failed to load constellation data:', err);
        document.getElementById('loading-screen').innerHTML = `
            <div class="logo">C<span class="star">\u2726</span>nstellation</div>
            <div style="color: var(--text-secondary); margin-top: 20px;">
                Failed to load data. Run the embedding pipeline first.
            </div>`;
    }
}

function updateTopBar(stats) {
    const statsEl = document.querySelector('#top-bar .stats');
    if (statsEl && stats) {
        statsEl.textContent = `${stats.totalConversations.toLocaleString()} conversations \u00b7 ${stats.totalMessages.toLocaleString()} messages \u00b7 ${stats.dateRange[0]} to ${stats.dateRange[1]}`;
    }
}

function updateStatusBar(visibleCount) {
    if (!appData) return;
    const stats = appData.stats;
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };

    set('stat-nodes', visibleCount.toLocaleString());
    set('stat-edges', (stats.edgeCount || appData.edges.length).toLocaleString());
    set('stat-clusters', stats.clusterCount);
    const providerCount = new Set(appData.nodes.map(n => n.provider || 'claude')).size;
    set('stat-providers', providerCount);
    set('stat-messages', stats.totalMessages.toLocaleString());
    set('stat-dim', stats.embeddingDim);
    set('stat-model', stats.embeddingModel);

    if (stats.dateRange && stats.dateRange.length === 2) {
        const fmt = d => d.substring(0, 7); // YYYY-MM
        set('stat-range', `${fmt(stats.dateRange[0])} \u2192 ${fmt(stats.dateRange[1])}`);
    }
}

var PROVIDER_COLORS = {
    claude: '#7957d9',
    chatgpt: '#10a37f',
    gemini: '#d9a857',
    grok: '#d97957',
};
var PROVIDER_LABELS = {
    claude: 'Claude',
    chatgpt: 'ChatGPT',
    gemini: 'Gemini',
    grok: 'Grok',
};

function buildProviderList() {
    var container = document.getElementById('provider-list');
    var section = document.getElementById('provider-section');
    if (!container || !section || !appData) return;

    var providerCounts = {};
    appData.nodes.forEach(function(n) {
        var p = n.provider || 'claude';
        providerCounts[p] = (providerCounts[p] || 0) + 1;
    });

    var providers = Object.keys(providerCounts);
    if (providers.length <= 1) {
        section.style.display = 'none';
        return;
    }
    section.style.display = 'block';

    container.innerHTML = providers.map(function(p) {
        return '<div class="cluster-item ' + (filteredProviders.has(p) ? 'disabled' : '') + '"'
             + ' onclick="toggleProvider(\'' + p + '\')">'
             + '<span class="cluster-dot" style="background: ' + (PROVIDER_COLORS[p] || '#888') + '"></span>'
             + '<span>' + (PROVIDER_LABELS[p] || p) + '</span>'
             + '<span class="cluster-count">' + providerCounts[p] + '</span>'
             + '</div>';
    }).join('');
}

function toggleProvider(provider) {
    if (filteredProviders.has(provider)) {
        filteredProviders.delete(provider);
    } else {
        filteredProviders.add(provider);
    }
    buildProviderList();
    applyFilters();
}

function buildClusterList() {
    const container = document.getElementById('cluster-list');
    if (!container || !appData) return;

    container.innerHTML = appData.clusters.map(c => `
        <div class="cluster-item ${filteredClusters.has(c.id) ? 'disabled' : ''}"
             onclick="toggleCluster(${c.id})">
            <span class="cluster-dot" style="background: ${c.color}"></span>
            <span>${escapeHtml(c.label)}</span>
            <span class="cluster-count">${c.count}</span>
        </div>
    `).join('');
}

function toggleCluster(clusterId) {
    if (filteredClusters.has(clusterId)) {
        filteredClusters.delete(clusterId);
    } else {
        filteredClusters.add(clusterId);
    }
    buildClusterList();
    applyFilters();
}

function buildControls() {
    // Layout selector
    const layoutSelect = document.getElementById('layout-select');
    if (layoutSelect) {
        layoutSelect.addEventListener('change', e => {
            setLayout(e.target.value);
        });
    }

    // K slider
    const kSlider = document.getElementById('k-slider');
    const kValue = document.getElementById('k-value');
    if (kSlider && appData) {
        kSlider.value = appData.stats.clusterCount;
        kValue.textContent = appData.stats.clusterCount;
        kSlider.addEventListener('input', e => {
            kValue.textContent = e.target.value;
        });
        kSlider.addEventListener('change', async e => {
            const k = parseInt(e.target.value);
            try {
                const resp = await fetch(`/api/recluster?k=${k}`);
                const newData = await resp.json();
                if (newData.nodes) {
                    appData = newData;
                    appData._allEdges = [...appData.edges];
                    graphData = appData;
                    const currentPercentile = parseInt(document.getElementById('edge-slider').value);
                    updateEdgePercentile(currentPercentile);
                    buildClusterList();
                    updateStatusBar(appData.nodes.length);
                }
            } catch (err) {
                console.error('Recluster failed:', err);
            }
        });
    }

    // Edge percentile slider
    const edgeSlider = document.getElementById('edge-slider');
    const edgeValue = document.getElementById('edge-value');
    if (edgeSlider) {
        edgeSlider.addEventListener('input', e => {
            edgeValue.textContent = e.target.value + '%';
        });
        edgeSlider.addEventListener('change', e => {
            updateEdgePercentile(parseInt(e.target.value));
        });
    }

    // Colorblind toggle
    const cbToggle = document.getElementById('colorblind-toggle');
    if (cbToggle) {
        cbToggle.addEventListener('change', e => {
            applyPalette(e.target.checked ? 'colorblind' : 'default');
        });
    }

    // Auto-rotate toggle
    const arToggle = document.getElementById('autorotate-toggle');
    if (arToggle && Graph) {
        arToggle.addEventListener('change', e => {
            window.isAutoRotating = e.target.checked;
        });
    }

    // Settings
    const settingsBtn = document.getElementById('settings-btn');
    const settingsOverlay = document.getElementById('settings-overlay');
    if (settingsBtn && settingsOverlay) {
        settingsBtn.addEventListener('click', () => openSettings());
        settingsOverlay.addEventListener('click', e => {
            if (e.target === settingsOverlay) closeSettings();
        });
    }
}

function openSettings() {
    const overlay = document.getElementById('settings-overlay');
    overlay.classList.add('open');

    // Populate settings data
    if (appData) {
        const stats = appData.stats;
        const dataStats = document.getElementById('settings-data-stats');
        if (dataStats) {
            dataStats.innerHTML = `
                <div class="data-stat">
                    <div class="label">Conversations</div>
                    <div class="value">${stats.totalConversations.toLocaleString()}</div>
                </div>
                <div class="data-stat">
                    <div class="label">Messages</div>
                    <div class="value">${stats.totalMessages.toLocaleString()}</div>
                </div>
                <div class="data-stat">
                    <div class="label">Date Range</div>
                    <div class="value">${stats.dateRange[0]} to ${stats.dateRange[1]}</div>
                </div>
                <div class="data-stat">
                    <div class="label">Embedding Model</div>
                    <div class="value">${stats.embeddingModel} (${stats.embeddingDim}d)</div>
                </div>
                <div class="data-stat">
                    <div class="label">Clusters</div>
                    <div class="value">${stats.clusterCount}</div>
                </div>
                <div class="data-stat">
                    <div class="label">Edges</div>
                    <div class="value">${stats.edgeCount.toLocaleString()}</div>
                </div>`;
        }
    }
}

function closeSettings() {
    document.getElementById('settings-overlay').classList.remove('open');
}

function copyMcpConfig() {
    const pre = document.querySelector('.mcp-config pre');
    if (pre) {
        navigator.clipboard.writeText(pre.textContent).then(() => {
            const btn = document.querySelector('.mcp-config .copy-btn');
            btn.textContent = 'Copied!';
            setTimeout(() => { btn.textContent = 'Copy'; }, 2000);
        });
    }
}

window.takeScreenshot = async function () {
    const btn = document.getElementById('screenshot-btn');
    if (!btn) return;
    const originalText = btn.innerHTML;
    try {
        btn.innerHTML = 'Capturing...';

        // Use the Promise pattern for ClipboardItem to satisfy browser security (needs user gesture)
        const blobPromise = new Promise(resolve => {
            // Force synchronous render pass to guarantee buffer isn't empty (fixes clear WebGL buffer issue)
            if (Graph) {
                Graph.renderer().render(Graph.scene(), Graph.camera());
            }
            const canvas = document.querySelector('#graph-viewport canvas');
            if (canvas) {
                canvas.toBlob(resolve, 'image/png');
            } else {
                throw new Error("Canvas not found");
            }
        });

        await navigator.clipboard.write([
            new ClipboardItem({ 'image/png': blobPromise })
        ]);

        btn.innerHTML = 'Copied! &#10003;';
    } catch (err) {
        console.error('Screenshot failed:', err);
        btn.innerHTML = 'Failed &#10060;';
    }
    setTimeout(() => { btn.innerHTML = originalText; }, 2000);
};

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', init);
