/* ============================================================
 *  M-STATUS · animated particle / constellation background
 *  Pure vanilla canvas — no deps. Auto-pauses when tab hidden.
 * ============================================================ */
(() => {
    const canvas = document.getElementById('bg-canvas');
    if (!canvas) return;

    const ctx = canvas.getContext('2d', { alpha: true });
    const PURPLE = 'rgba(140, 82, 255, ';
    const ORANGE = 'rgba(255, 145, 77, ';

    let w = 0, h = 0, dpr = 1;
    let particles = [];
    let mouse = { x: -9999, y: -9999, active: false };
    let rafId = null;
    let running = true;

    function resize() {
        dpr = Math.min(window.devicePixelRatio || 1, 2);
        w = window.innerWidth;
        h = window.innerHeight;
        canvas.width = Math.floor(w * dpr);
        canvas.height = Math.floor(h * dpr);
        canvas.style.width = w + 'px';
        canvas.style.height = h + 'px';
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        rebuild();
    }

    function rebuild() {
        // Density ~ one particle per ~14k px², capped to keep mobile happy.
        const target = Math.min(120, Math.max(30, Math.round((w * h) / 14000)));
        particles = new Array(target).fill(null).map(() => spawn());
    }

    function spawn() {
        return {
            x: Math.random() * w,
            y: Math.random() * h,
            vx: (Math.random() - 0.5) * 0.25,
            vy: (Math.random() - 0.5) * 0.25,
            r: 0.8 + Math.random() * 1.4,
            tone: Math.random() < 0.5 ? PURPLE : ORANGE,
            alpha: 0.35 + Math.random() * 0.5,
        };
    }

    function step() {
        if (!running) return;
        ctx.clearRect(0, 0, w, h);

        // Move + draw points
        for (const p of particles) {
            p.x += p.vx;
            p.y += p.vy;
            if (p.x < -10) p.x = w + 10;
            if (p.x > w + 10) p.x = -10;
            if (p.y < -10) p.y = h + 10;
            if (p.y > h + 10) p.y = -10;

            ctx.beginPath();
            ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
            ctx.fillStyle = p.tone + p.alpha + ')';
            ctx.fill();
        }

        // Connection lines — only short edges so it stays sparse
        const MAX = 130;
        const MAX2 = MAX * MAX;
        for (let i = 0; i < particles.length; i++) {
            const a = particles[i];
            for (let j = i + 1; j < particles.length; j++) {
                const b = particles[j];
                const dx = a.x - b.x;
                const dy = a.y - b.y;
                const d2 = dx * dx + dy * dy;
                if (d2 > MAX2) continue;
                const opacity = (1 - d2 / MAX2) * 0.18;
                ctx.beginPath();
                ctx.moveTo(a.x, a.y);
                ctx.lineTo(b.x, b.y);
                ctx.strokeStyle = (i % 2 === 0 ? PURPLE : ORANGE) + opacity + ')';
                ctx.lineWidth = 0.6;
                ctx.stroke();
            }
        }

        // Mouse halo — gentle attraction line
        if (mouse.active) {
            for (const p of particles) {
                const dx = p.x - mouse.x;
                const dy = p.y - mouse.y;
                const d2 = dx * dx + dy * dy;
                if (d2 > 22500) continue;
                const o = (1 - d2 / 22500) * 0.35;
                ctx.beginPath();
                ctx.moveTo(mouse.x, mouse.y);
                ctx.lineTo(p.x, p.y);
                ctx.strokeStyle = PURPLE + o + ')';
                ctx.lineWidth = 0.7;
                ctx.stroke();
            }
        }

        rafId = requestAnimationFrame(step);
    }

    function pause()  { running = false; if (rafId) cancelAnimationFrame(rafId); rafId = null; }
    function resume() { if (running) return; running = true; rafId = requestAnimationFrame(step); }

    // Listeners
    window.addEventListener('resize', resize, { passive: true });
    window.addEventListener('mousemove', e => {
        mouse.x = e.clientX; mouse.y = e.clientY; mouse.active = true;
    }, { passive: true });
    window.addEventListener('mouseleave', () => { mouse.active = false; });
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) pause(); else resume();
    });

    resize();
    rafId = requestAnimationFrame(step);
})();
