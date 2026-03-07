/**
 * 3d-force-graph wrapper for Constellation V3.
 * Custom node rendering, particle edges, layout modes.
 */

let Graph = null;
let graphData = null;
let activeLayout = '3d-force';
let filteredClusters = new Set();
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
        .nodeLabel(node => `${node.name}\n${node.messageCount} messages \u00b7 ${node.date}`)
        .nodeColor(node => node.color)
        .nodeVal(node => Math.log(node.messageCount + 1) * 3)
        .nodeOpacity(0.9)
        .linkSource('source')
        .linkTarget('target')
        .linkWidth(link => link.weight * 2)
        .linkOpacity(0.15)
        .linkDirectionalParticles(link => Math.ceil(link.weight * 4))
        .linkDirectionalParticleSpeed(0.002)
        .linkDirectionalParticleWidth(1)
        .linkDirectionalParticleColor(() => 'rgba(121, 87, 217, 0.6)')
        .backgroundColor('rgba(0,0,0,0)')
        .onNodeClick(handleNodeClick)
        .onNodeHover(handleNodeHover)
        .enableNodeDrag(true)
        .cooldownTime(3000)
        .d3AlphaDecay(0.02)
        .d3VelocityDecay(0.3);

    // Custom node rendering with emissive glow (no sprites — avoids rectangular halo artifacts)
    Graph.nodeThreeObject(node => {
        const size = nodeSize(node);
        const geometry = new THREE.SphereGeometry(size, 16, 16);
        const material = new THREE.MeshPhongMaterial({
            color: node.color,
            emissive: node.color,
            emissiveIntensity: 0.4,
            transparent: true,
            opacity: 0.85
        });
        return new THREE.Mesh(geometry, material);
    });

    // Auto-rotate camera
    const controls = Graph.controls();
    controls.autoRotate = true;
    controls.autoRotateSpeed = 0.5;

    // Pause auto-rotate during user interaction, resume after 3s idle
    let interactionTimer = null;
    container.addEventListener('mousedown', () => {
        if (controls.autoRotate) {
            controls.autoRotate = false;
            clearTimeout(interactionTimer);
        }
    });
    container.addEventListener('mouseup', () => {
        const toggle = document.getElementById('autorotate-toggle');
        if (toggle && toggle.checked) {
            clearTimeout(interactionTimer);
            interactionTimer = setTimeout(() => { controls.autoRotate = true; }, 3000);
        }
    });
    container.addEventListener('wheel', () => {
        const toggle = document.getElementById('autorotate-toggle');
        if (toggle && toggle.checked) {
            controls.autoRotate = false;
            clearTimeout(interactionTimer);
            interactionTimer = setTimeout(() => { controls.autoRotate = true; }, 3000);
        }
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
        const activeIds = new Set();
        nodes = nodes.filter(n => {
            if (!filteredClusters.has(n.cluster)) {
                activeIds.add(n.id);
                return true;
            }
            return false;
        });
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

function handleNodeClick(node) {
    if (!node) return;
    showInspector(node);
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

    Graph.nodeOpacity(node => idSet.has(node.id) ? 1.0 : 0.15);
    Graph.linkOpacity(link => {
        const src = typeof link.source === 'object' ? link.source.id : link.source;
        const tgt = typeof link.target === 'object' ? link.target.id : link.target;
        return (idSet.has(src) && idSet.has(tgt)) ? 0.3 : 0.02;
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
    Graph.nodeOpacity(0.9);
    Graph.linkOpacity(0.15);
}

// Layout modes
function setLayout(mode) {
    activeLayout = mode;
    if (!Graph || !graphData) return;

    // Clear any pinned positions
    if (mode !== 'temporal-helix') {
        graphData.nodes.forEach(node => {
            node.fx = undefined;
            node.fy = undefined;
            node.fz = undefined;
        });
    }

    switch (mode) {
        case '3d-force':
            Graph.cooldownTicks(Infinity); // Restore simulation (temporal-helix sets to 0)
            Graph.numDimensions(3);
            Graph.d3Force('charge').strength(-80);
            Graph.d3Force('link').distance(link => (1 - link.weight) * 100);
            // Re-set graphData to restart the simulation
            Graph.graphData(Graph.graphData());
            break;

        case '2d-flat':
            Graph.cooldownTicks(Infinity);
            Graph.numDimensions(2);
            Graph.d3Force('charge').strength(-60);
            Graph.graphData(Graph.graphData());
            break;

        case 'by-cluster':
            Graph.cooldownTicks(Infinity);
            Graph.numDimensions(3);
            Graph.d3Force('charge').strength(-30);
            // Apply cluster force via initial positions
            const clusterPositions = {};
            const k = graphData.clusters.length;
            graphData.clusters.forEach((c, i) => {
                const phi = (i / k) * Math.PI * 2;
                const theta = Math.PI / 2 + (i % 3 - 1) * 0.5;
                clusterPositions[c.id] = {
                    x: Math.cos(phi) * Math.sin(theta) * 80,
                    y: Math.cos(theta) * 80,
                    z: Math.sin(phi) * Math.sin(theta) * 80
                };
            });
            graphData.nodes.forEach(node => {
                const cp = clusterPositions[node.cluster];
                if (cp) {
                    node.x = cp.x + (Math.random() - 0.5) * 30;
                    node.y = cp.y + (Math.random() - 0.5) * 30;
                    node.z = cp.z + (Math.random() - 0.5) * 30;
                }
            });
            Graph.graphData(Graph.graphData());
            break;

        case 'temporal-helix':
            Graph.cooldownTicks(0);
            graphData.nodes.forEach(node => {
                node.fx = node.tx;
                node.fy = node.ty;
                node.fz = node.tz;
            });
            Graph.refresh();
            break;
    }
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
