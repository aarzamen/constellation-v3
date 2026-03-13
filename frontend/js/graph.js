/**
 * 3d-force-graph wrapper for Constellation.
 * Custom node rendering, particle edges, layout modes.
 */

let Graph = null;
let graphData = null;
let activeLayout = '3d-force';
let filteredClusters = new Set();
let filteredProviders = new Set();
let timelineFilter = null;

function nodeSize(node) {
    return Math.max(2, Math.log(node.messageCount + 1) * 1.5);
}

function initGraph(data) {
    graphData = data;

    const container = document.getElementById('graph-viewport');

    Graph = ForceGraph3D()(container)
        .graphData({ nodes: [], links: [] })
        .nodeId('id')
        .nodeLabel(node => {
            const providerLabel = node.provider === 'chatgpt' ? 'ChatGPT'
                                : node.provider === 'gemini' ? 'Gemini'
                                : node.provider === 'grok' ? 'Grok'
                                : 'Claude';
            return `${node.name}\n${providerLabel} \u00b7 ${node.messageCount} messages \u00b7 ${node.date}`;
        })
        .nodeColor(node => node.color)
        .nodeVal(node => Math.log(node.messageCount + 1) * 3)
        .nodeOpacity(0.9)
        .linkSource('source')
        .linkTarget('target')
        .linkWidth(link => link.weight * 2)
        .linkOpacity(0.04)
        .linkDirectionalParticles(link => Math.ceil(link.weight * 4))
        .linkDirectionalParticleSpeed(0.002)
        .linkDirectionalParticleWidth(1)
        .linkDirectionalParticleColor(() => 'rgba(121, 87, 217, 0.4)')
        .backgroundColor('rgba(0,0,0,0)')
        .onNodeClick(handleNodeClick)
        .onNodeHover(handleNodeHover)
        .enableNodeDrag(true)
        .cooldownTime(3000)
        .d3AlphaDecay(0.02)
        .d3VelocityDecay(0.3);

    // Custom node rendering with LOD (Level of Detail)
    // Shape encodes provider: Claude=Sphere, ChatGPT=Octahedron, Gemini=Dodecahedron, Grok=Icosahedron
    Graph.nodeThreeObject(node => {
        const size = nodeSize(node);
        const lod = new THREE.LOD();

        const isGPT = node.provider === 'chatgpt';
        const isGemini = node.provider === 'gemini';
        const isGrok = node.provider === 'grok';

        // High detail: glossy phong material
        const highGeo = isGemini ? new THREE.DodecahedronGeometry(size)
                      : isGrok ? new THREE.IcosahedronGeometry(size)
                      : isGPT ? new THREE.OctahedronGeometry(size)
                      : new THREE.SphereGeometry(size, 16, 16);
        const highMat = new THREE.MeshPhongMaterial({
            color: node.color,
            emissive: node.color,
            emissiveIntensity: 0.5,
            transparent: true,
            opacity: 0.85
        });
        const highMesh = new THREE.Mesh(highGeo, highMat);
        lod.addLevel(highMesh, 0);

        // Low detail: basic reduced geometry for massive scale performance
        const lowGeo = isGemini ? new THREE.DodecahedronGeometry(size)
                     : isGrok ? new THREE.IcosahedronGeometry(size)
                     : isGPT ? new THREE.OctahedronGeometry(size)
                     : new THREE.SphereGeometry(size, 5, 5);
        const lowMat = new THREE.MeshBasicMaterial({
            color: node.color,
            transparent: true,
            opacity: 0.6
        });
        const lowMesh = new THREE.Mesh(lowGeo, lowMat);
        lod.addLevel(lowMesh, 200);

        return lod;
    });

    // Auto-rotate custom loop (fixes render suspension bug)
    window.isAutoRotating = true;
    let cameraPaused = false;

    function rotateTick() {
        if (window.isAutoRotating && !cameraPaused && Graph) {
            const camPos = Graph.cameraPosition();
            if (camPos && (camPos.x !== 0 || camPos.z !== 0)) {
                const distance = Math.hypot(camPos.x, camPos.z);
                const currentAngle = Math.atan2(camPos.x, camPos.z);
                const nextAngle = currentAngle + Math.PI / 1500;
                Graph.cameraPosition({
                    x: distance * Math.sin(nextAngle),
                    z: distance * Math.cos(nextAngle),
                    y: camPos.y // preserve height
                });
            }
        }
        requestAnimationFrame(rotateTick);
    }
    rotateTick();

    // Pause auto-rotate during user interaction, resume after 3s idle
    let interactionTimer = null;
    function pauseRotation() {
        cameraPaused = true;
        clearTimeout(interactionTimer);
    }
    function resumeRotation() {
        clearTimeout(interactionTimer);
        interactionTimer = setTimeout(() => { cameraPaused = false; }, 3000);
    }

    container.addEventListener('mousedown', pauseRotation);
    container.addEventListener('mouseup', resumeRotation);
    container.addEventListener('wheel', () => {
        pauseRotation();
        resumeRotation();
    });

    // Load data
    applyFilters();
}

function applyFilters() {
    if (!graphData) return;

    // Save current positions before filtering (3d-force-graph mutates nodes in place)
    graphData.nodes.forEach(node => {
        if (node.x !== undefined) node._savedX = node.x;
        if (node.y !== undefined) node._savedY = node.y;
        if (node.z !== undefined) node._savedZ = node.z;
    });

    let nodes = graphData.nodes;
    let edges = graphData.edges;

    // Filter by cluster
    if (filteredClusters.size > 0) {
        nodes = nodes.filter(n => !filteredClusters.has(n.cluster));
    }

    // Filter by provider
    if (filteredProviders.size > 0) {
        nodes = nodes.filter(n => !filteredProviders.has(n.provider || 'claude'));
    }

    // Re-filter edges to match remaining nodes
    if (filteredClusters.size > 0 || filteredProviders.size > 0) {
        const activeIds = new Set(nodes.map(n => n.id));
        edges = edges.filter(e => activeIds.has(e.source) && activeIds.has(e.target));
    }

    // Filter by timeline
    if (timelineFilter) {
        const activeIds = new Set();
        nodes = nodes.filter(n => {
            if (n.date >= timelineFilter.start && n.date <= timelineFilter.end) {
                activeIds.add(n.id);
                return true;
            }
            return false;
        });
        edges = edges.filter(e =>
            activeIds.has(typeof e.source === 'object' ? e.source.id : e.source) &&
            activeIds.has(typeof e.target === 'object' ? e.target.id : e.target)
        );
    }

    // Restore saved positions so re-added nodes appear where they were
    nodes.forEach(node => {
        if (node._savedX !== undefined) {
            node.x = node._savedX;
            node.y = node._savedY;
            node.z = node._savedZ;
        }
    });

    Graph.graphData({
        nodes: nodes,
        links: edges.map(e => ({
            source: typeof e.source === 'object' ? e.source.id : e.source,
            target: typeof e.target === 'object' ? e.target.id : e.target,
            weight: e.weight,
        }))
    });

    // graphData() call above restarts the simulation automatically

    updateStatusBar(nodes.length);
}

function handleNodeClick(node, query = '') {
    if (!node) return;
    showInspector(node, query);
    // Fly camera to node
    const distance = 60;
    const nx = node.x || 0.1;
    const ny = node.y || 0.1;
    const nz = node.z || 0.1;
    const distRatio = 1 + distance / Math.hypot(nx, ny, nz);
    Graph.cameraPosition(
        { x: nx * distRatio, y: ny * distRatio, z: nz * distRatio },
        node,
        1000
    );
}

function handleNodeHover(node) {
    document.getElementById('graph-viewport').style.cursor = node ? 'pointer' : 'default';
}

function highlightNodes(nodeIds) {
    const idSet = new Set(nodeIds);
    if (!Graph || !graphData) return;

    // Stronger contrast: Unfocused nodes drop to 0.05 opacity, focused to 1.0
    Graph.nodeOpacity(node => idSet.has(node.id) ? 1.0 : 0.05);
    Graph.linkOpacity(link => {
        const src = typeof link.source === 'object' ? link.source.id : link.source;
        const tgt = typeof link.target === 'object' ? link.target.id : link.target;
        // Unfocused edges disappear entirely (0.01), focused become highly visible (0.6)
        return (idSet.has(src) && idSet.has(tgt)) ? 0.6 : 0.01;
    });

    // Fly to first result
    if (nodeIds.length > 0) {
        const firstNode = graphData.nodes.find(n => n.id === nodeIds[0]);
        if (firstNode) {
            handleNodeClick(firstNode);
        }
    }
}

function clearHighlight() {
    if (!Graph) return;
    Graph.nodeOpacity(0.85); // Matches high detail mesh opacity
    Graph.linkOpacity(0.04); // Matches initGraph base opacity
}

// Layout modes
function setLayout(mode) {
    activeLayout = mode;
    if (!Graph || !graphData) return;

    // ALWAYS clear ALL pinned positions first (fixes stuck pins from temporal-helix)
    graphData.nodes.forEach(node => {
        node.fx = undefined;
        node.fy = undefined;
        node.fz = undefined;
    });

    // Helper: build clean link objects (3d-force-graph mutates source/target to objects)
    var buildCleanLinks = function() {
        return graphData.edges.map(function(e) {
            return {
                source: typeof e.source === 'object' ? e.source.id : e.source,
                target: typeof e.target === 'object' ? e.target.id : e.target,
                weight: e.weight,
            };
        });
    };

    switch (mode) {
        case '3d-force':
            Graph.numDimensions(3);
            Graph.cooldownTicks(Infinity);
            Graph.d3Force('charge').strength(-80);
            Graph.d3Force('link').distance(function(link) { return (1 - (link.weight || 0.5)) * 100; });
            // Perturb positions slightly so simulation has energy after switching from pinned modes
            graphData.nodes.forEach(function(n) {
                n.x = (n.x || 0) + (Math.random() - 0.5) * 5;
                n.y = (n.y || 0) + (Math.random() - 0.5) * 5;
                n.z = (n.z || 0) + (Math.random() - 0.5) * 5;
            });
            Graph.graphData({ nodes: graphData.nodes, links: buildCleanLinks() });
            break;

        case '2d-flat':
            Graph.numDimensions(2);
            Graph.cooldownTicks(Infinity);
            // Much stronger repulsion in 2D to prevent piling with many nodes
            Graph.d3Force('charge').strength(-150);
            Graph.d3Force('link').distance(function(link) { return (1 - (link.weight || 0.5)) * 150; });
            // Zero out z positions
            graphData.nodes.forEach(function(n) { n.z = 0; });
            Graph.graphData({ nodes: graphData.nodes, links: buildCleanLinks() });
            break;

        case 'by-cluster':
            Graph.numDimensions(3);
            Graph.cooldownTicks(Infinity);

            // Scale cluster separation based on node count
            var nodeCount = graphData.nodes.length;
            var clusterRadius = Math.max(120, Math.sqrt(nodeCount) * 5);
            var jitter = clusterRadius * 0.4;

            var k = graphData.clusters.length;
            var clusterPositions = {};
            graphData.clusters.forEach(function(c, i) {
                var phi = (i / k) * Math.PI * 2;
                var theta = Math.PI / 2 + (i % 3 - 1) * 0.4;
                clusterPositions[c.id] = {
                    x: Math.cos(phi) * Math.sin(theta) * clusterRadius,
                    y: Math.cos(theta) * clusterRadius,
                    z: Math.sin(phi) * Math.sin(theta) * clusterRadius
                };
            });

            graphData.nodes.forEach(function(node) {
                var cp = (node.cluster >= 0 && node.cluster < k)
                    ? clusterPositions[node.cluster]
                    : { x: 0, y: 0, z: 0 };
                node.x = cp.x + (Math.random() - 0.5) * jitter;
                node.y = cp.y + (Math.random() - 0.5) * jitter;
                node.z = cp.z + (Math.random() - 0.5) * jitter;
            });

            // Moderate repulsion so nodes spread within clusters
            Graph.d3Force('charge').strength(-50);
            Graph.d3Force('link').distance(function(link) {
                return 40;
            });
            Graph.graphData({ nodes: graphData.nodes, links: buildCleanLinks() });
            break;

        case 'temporal-helix':
            Graph.numDimensions(3);
            Graph.cooldownTicks(0);

            // Scale temporal positions to spread nodes out more
            var txScale = 2.0;
            var tyScale = 1.5;
            graphData.nodes.forEach(function(node) {
                node.fx = (node.tx || 0) * txScale;
                node.fy = (node.ty || 0) * tyScale;
                node.fz = (node.tz || 0) * tyScale;
                node.x = node.fx;
                node.y = node.fy;
                node.z = node.fz;
            });

            Graph.graphData({ nodes: graphData.nodes, links: buildCleanLinks() });

            // Frame the helix: camera looking along the timeline axis
            setTimeout(function() {
                Graph.cameraPosition(
                    { x: 0, y: 200, z: 300 },
                    { x: 0, y: 0, z: 0 },
                    1500
                );
            }, 100);
            break;
    }

    // Auto-reframe camera after layout switch (helix has its own camera)
    if (mode !== 'temporal-helix') {
        setTimeout(function() { centerView(); }, 500);
    }
}

function centerView() {
    if (!Graph || !graphData || !graphData.nodes.length) return;
    // Compute bounding box center and fly camera to encompass all nodes
    var xs = graphData.nodes.map(function(n) { return n.x || 0; });
    var ys = graphData.nodes.map(function(n) { return n.y || 0; });
    var zs = graphData.nodes.map(function(n) { return n.z || 0; });
    var cx = (Math.min.apply(null, xs) + Math.max.apply(null, xs)) / 2;
    var cy = (Math.min.apply(null, ys) + Math.max.apply(null, ys)) / 2;
    var cz = (Math.min.apply(null, zs) + Math.max.apply(null, zs)) / 2;
    var spread = Math.max(
        Math.max.apply(null, xs) - Math.min.apply(null, xs),
        Math.max.apply(null, ys) - Math.min.apply(null, ys),
        Math.max.apply(null, zs) - Math.min.apply(null, zs)
    );
    var dist = Math.max(spread * 1.2, 150);
    Graph.cameraPosition(
        { x: cx, y: cy + dist * 0.3, z: cz + dist },
        { x: cx, y: cy, z: cz },
        1500
    );
}

function updateEdgePercentile(percentile) {
    if (!graphData || !graphData._allEdges) {
        graphData._allEdges = [...graphData.edges];
    }
    // Re-filter edges based on percentile
    const weights = graphData._allEdges.map(e => e.weight);
    weights.sort((a, b) => a - b);
    const threshIdx = Math.floor(weights.length * percentile / 100);
    const threshold = weights[threshIdx] || 0;

    graphData.edges = graphData._allEdges.filter(e => e.weight >= threshold);
    applyFilters();
}

function applyPalette(paletteName) {
    if (!graphData) return;
    const DEFAULT_PALETTE = [
        '#7957d9', '#57b4d9', '#d9a857', '#57d98b', '#d957a8',
        '#d97957', '#57d9d9', '#a857d9', '#d9d957', '#5779d9',
        '#d95757', '#57d957', '#d957d9', '#8b8bd9', '#d98b57',
        '#57d9a8', '#d95779', '#79d957', '#5757d9', '#d9a8a8'
    ];
    const COLORBLIND_PALETTE = [
        '#0072B2', '#E69F00', '#56B4E9', '#009E73', '#F0E442',
        '#CC79A7', '#D55E00', '#0072B2', '#E69F00', '#56B4E9',
        '#009E73', '#F0E442', '#CC79A7', '#D55E00', '#0072B2',
        '#E69F00', '#56B4E9', '#009E73', '#F0E442', '#CC79A7'
    ];
    const palette = paletteName === 'colorblind' ? COLORBLIND_PALETTE : DEFAULT_PALETTE;

    graphData.clusters.forEach((cluster, i) => {
        cluster.color = palette[i % palette.length];
    });
    graphData.nodes.forEach(node => {
        node.color = graphData.clusters[node.cluster].color;
    });
    Graph.nodeColor(node => node.color);
    Graph.nodeThreeObject(Graph.nodeThreeObject()); // Force re-render
    buildClusterList();
}
