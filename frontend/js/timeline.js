/**
 * Brushable timeline for Constellation.
 * Renders monthly conversation counts with cluster-colored bars.
 */

let timelineData = null;
let brushStart = null;
let brushEnd = null;
let isDragging = false;

function initTimeline(data, clusters) {
    timelineData = data;
    if (!timelineData || timelineData.length === 0) return;

    const canvas = document.getElementById('timeline-canvas');
    if (!canvas) return;

    renderTimeline(canvas, clusters);

    // Brush interaction
    canvas.addEventListener('mousedown', e => {
        isDragging = true;
        brushStart = getTimelinePosition(canvas, e);
        brushEnd = brushStart;
    });

    canvas.addEventListener('mousemove', e => {
        if (!isDragging) return;
        brushEnd = getTimelinePosition(canvas, e);
        renderTimeline(canvas, clusters);
        renderBrush(canvas);
    });

    canvas.addEventListener('mouseup', () => {
        isDragging = false;
        if (brushStart !== null && brushEnd !== null) {
            const startIdx = Math.min(brushStart, brushEnd);
            const endIdx = Math.max(brushStart, brushEnd);
            if (endIdx - startIdx < 2) {
                // Too small, clear filter
                timelineFilter = null;
                clearHighlight();
            } else {
                applyTimelineBrush(startIdx, endIdx);
            }
        }
        applyFilters();
    });

    // Double-click to clear brush
    canvas.addEventListener('dblclick', () => {
        brushStart = null;
        brushEnd = null;
        timelineFilter = null;
        renderTimeline(canvas, clusters);
        clearHighlight();
        applyFilters();
    });
}

function getTimelinePosition(canvas, event) {
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    return Math.floor((x / rect.width) * (timelineData.length || 1));
}

function applyTimelineBrush(startIdx, endIdx) {
    if (!timelineData) return;
    const startMonth = timelineData[Math.max(0, startIdx)];
    const endMonth = timelineData[Math.min(timelineData.length - 1, endIdx)];
    if (startMonth && endMonth) {
        timelineFilter = {
            start: startMonth.month + '-01',
            end: endMonth.month + '-31'
        };
    }
}

function renderTimeline(canvas, clusters) {
    if (!timelineData || timelineData.length === 0) return;

    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);

    const w = rect.width;
    const h = rect.height;
    const padding = { left: 40, right: 10, top: 5, bottom: 18 };
    const chartW = w - padding.left - padding.right;
    const chartH = h - padding.top - padding.bottom;

    ctx.clearRect(0, 0, w, h);

    const maxCount = Math.max(...timelineData.map(d => d.count));
    const barWidth = Math.max(2, chartW / timelineData.length - 1);

    // Build cluster color map
    const clusterColors = {};
    if (clusters) {
        clusters.forEach(c => { clusterColors[c.id] = c.color; });
    }

    timelineData.forEach((month, i) => {
        const x = padding.left + (i / timelineData.length) * chartW;
        const totalHeight = (month.count / maxCount) * chartH;

        // Stacked bars by cluster
        let yOffset = 0;
        const breakdown = month.clusterBreakdown || {};
        const sortedClusters = Object.entries(breakdown).sort((a, b) => b[1] - a[1]);

        sortedClusters.forEach(([clusterId, count]) => {
            const segHeight = (count / month.count) * totalHeight;
            const color = clusterColors[parseInt(clusterId)] || '#7957d9';
            ctx.fillStyle = color;
            ctx.globalAlpha = 0.7;
            ctx.fillRect(x, padding.top + chartH - yOffset - segHeight, barWidth, segHeight);
            yOffset += segHeight;
        });
    });

    ctx.globalAlpha = 1;

    // X-axis labels (every ~6 months)
    ctx.fillStyle = '#5c5a6e';
    ctx.font = '10px Poppins, sans-serif';
    ctx.textAlign = 'center';
    const step = Math.max(1, Math.floor(timelineData.length / 6));
    for (let i = 0; i < timelineData.length; i += step) {
        const x = padding.left + (i / timelineData.length) * chartW + barWidth / 2;
        const label = timelineData[i].month.replace('-', '/').slice(2);
        ctx.fillText(label, x, h - 3);
    }

    // Brush overlay
    if (brushStart !== null && brushEnd !== null) {
        const s = Math.min(brushStart, brushEnd);
        const e = Math.max(brushStart, brushEnd);
        const bx = padding.left + (s / timelineData.length) * chartW;
        const bw = ((e - s) / timelineData.length) * chartW;
        ctx.fillStyle = 'rgba(121, 87, 217, 0.15)';
        ctx.fillRect(bx, padding.top, bw, chartH);
        ctx.strokeStyle = '#7957d9';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(bx, padding.top);
        ctx.lineTo(bx, padding.top + chartH);
        ctx.moveTo(bx + bw, padding.top);
        ctx.lineTo(bx + bw, padding.top + chartH);
        ctx.stroke();
    }
}

function renderBrush(canvas) {
    // Brush is rendered as part of renderTimeline
}
