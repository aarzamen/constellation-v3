/**
 * Starfield background renderer for Constellation.
 * Renders a static canvas with ~300 tiny dots of varying brightness.
 * Some dots pulse slowly via CSS animation.
 */

function renderStarfield(canvas) {
    const starCount = 300;
    canvas.width = canvas.offsetWidth * window.devicePixelRatio;
    canvas.height = canvas.offsetHeight * window.devicePixelRatio;
    const ctx = canvas.getContext('2d');
    ctx.scale(window.devicePixelRatio, window.devicePixelRatio);

    const w = canvas.offsetWidth;
    const h = canvas.offsetHeight;

    for (let i = 0; i < starCount; i++) {
        const x = Math.random() * w;
        const y = Math.random() * h;
        const brightness = 0.1 + Math.random() * 0.25;
        const radius = Math.random() < 0.05 ? 1.5 : 0.5;
        ctx.beginPath();
        ctx.arc(x, y, radius, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(232, 230, 240, ${brightness})`;
        ctx.fill();
    }
}

// Re-render on resize
function initStarfield() {
    const canvas = document.getElementById('starfield-canvas');
    if (!canvas) return;
    renderStarfield(canvas);

    let resizeTimeout;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimeout);
        resizeTimeout = setTimeout(() => renderStarfield(canvas), 200);
    });
}
