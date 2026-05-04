/* ============================================================
 *  M-STATUS · public status page
 *  Polls /api/public/status every 30s and re-renders.
 * ============================================================ */
(() => {
    const POLL_MS = 30_000;

    const els = {
        banner:          document.getElementById('overall-banner'),
        warningSlot:     document.getElementById('warning-banner-slot'),
        generatedAt:     document.getElementById('generated-at'),
        devicesSection:  document.getElementById('devices-section'),
        devicesCount:    document.getElementById('devices-count'),
        devicesContent:  document.getElementById('devices-content'),
        servicesCount:   document.getElementById('services-count'),
        servicesContent: document.getElementById('services-content'),
        brandSubtitle:   document.getElementById('brand-subtitle'),
        brandOwner:      document.getElementById('brand-owner'),
        brandManage:     document.getElementById('brand-manage'),
    };

    // ── helpers ────────────────────────────────────────────────────────
    const escapeHtml = (s) => String(s ?? '').replace(/[&<>"']/g, m => (
        { '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[m]
    ));

    function fmtRelative(ts) {
        if (!ts) return '—';
        const diff = (Date.now() / 1000) - ts;
        if (diff < 60)        return 'gerade eben';
        if (diff < 3600)      return `vor ${Math.floor(diff / 60)} Min`;
        if (diff < 86_400)    return `vor ${Math.floor(diff / 3600)} h`;
        return `vor ${Math.floor(diff / 86400)} Tagen`;
    }

    function fmtTimeShort(ts) {
        if (!ts) return '';
        const d = new Date(ts * 1000);
        return d.toLocaleTimeString('de-DE', { hour:'2-digit', minute:'2-digit' });
    }

    function fmtDay(yyyymmdd) {
        const [y, m, d] = yyyymmdd.split('-');
        return `${d}.${m}.${y}`;
    }

    const STATUS_LABELS_DAY = {
        green:  'Operational',
        orange: 'Probleme',
        red:    'Ausfall',
        grey:   'Keine Daten',
    };

    function bannerLabel(colour, hasItems) {
        if (!hasItems) return 'Noch keine Daten';
        if (colour === 'green')  return 'Alle Systeme operational';
        if (colour === 'orange') return 'Teilausfall — Probleme erkannt';
        if (colour === 'red')    return 'Ausfall — kritische Systeme offline';
        return 'Status unbekannt';
    }

    // ── history strip ─────────────────────────────────────────────────
    function renderStrip(history) {
        return history.map(d => {
            const tip = `${fmtDay(d.day)} · ${STATUS_LABELS_DAY[d.status] || d.status}`;
            return `<span class="day ${d.status}" data-tip="${escapeHtml(tip)}"></span>`;
        }).join('');
    }

    // ── entity row (device or service) ────────────────────────────────
    function renderRow(entity, kind) {
        const colour = entity.live;
        const liveLabel = entity.live_label || 'Unbekannt';

        let subline;
        if (kind === 'device') {
            const checkInfo = entity.last_check
                ? `Letzter Check ${fmtRelative(entity.last_check)}`
                : 'Noch kein Check';
            // When degraded/offline, show how many fails accumulated.
            const fails = entity.fail_runs || 0;
            const failPart = (colour !== 'green' && fails > 0)
                ? ` · ${fails}× in Folge nicht erreichbar`
                : '';
            subline = `${checkInfo}${failPart}`;
        } else {
            const tag = entity.is_local ? 'Lokal' : 'Extern';
            subline = `${tag} · ${escapeHtml(entity.url)}`;
        }

        const oldestDay = entity.history[0]?.day ? fmtDay(entity.history[0].day) : '';

        return `
            <div class="entity-row">
                <div class="entity-info">
                    <div class="entity-name">${escapeHtml(entity.name)}</div>
                    <div class="entity-meta">${subline}</div>
                </div>
                <div class="entity-pill ${colour}">
                    <span class="dot"></span>
                    <span>${escapeHtml(liveLabel)}</span>
                </div>
                <div class="history-strip" role="img" aria-label="${entity.history.length}-Tage Verlauf">
                    ${renderStrip(entity.history)}
                </div>
                <div class="uptime-row">
                    <span>${oldestDay}</span>
                    <span><span class="uptime-pct">${entity.uptime_pct.toFixed(2)}%</span> Uptime</span>
                    <span>heute</span>
                </div>
            </div>
        `;
    }

    function renderCategory(group, kind) {
        const rows = group.items.map(d => renderRow(d, kind)).join('');
        return `
            <div class="category-block">
                <div class="category-title">${escapeHtml(group.name)}</div>
                <div class="entity-list">${rows}</div>
            </div>
        `;
    }

    // ── overseer warning banner ───────────────────────────────────────
    function renderWarningBanner(data) {
        const o = data.overseer || {};
        if (!o.configured) return '';
        if (o.connected)   return '';
        return `
            <div class="warning-banner">
                <div>
                    <strong>Verbindung zum Overseer verloren</strong> ·
                    Geräte-Inventar kann veraltet sein. Pings laufen weiter normal.
                </div>
            </div>
        `;
    }

    // ── branding (only the editable bits — title is hardcoded) ────────
    function applyBranding(b) {
        if (!b) return;
        if (els.brandSubtitle && b.page_subtitle) {
            els.brandSubtitle.textContent = b.page_subtitle;
        }
        if (els.brandOwner && b.footer_owner) {
            els.brandOwner.textContent = b.footer_owner;
        }
        if (els.brandManage && b.manage_label) {
            els.brandManage.textContent = b.manage_label;
        }
    }

    // ── sorting ───────────────────────────────────────────────────────
    // Sort categories alphabetically (de locale) and items within each
    // category alphabetically by name. Doesn't mutate the original data.
    function sortGroups(groups) {
        return [...(groups || [])]
            .map(g => ({
                ...g,
                items: [...(g.items || [])].sort(
                    (a, b) => (a.name || '').localeCompare(b.name || '', 'de', { sensitivity: 'base' })
                ),
            }))
            .sort(
                (a, b) => (a.name || '').localeCompare(b.name || '', 'de', { sensitivity: 'base' })
            );
    }

    // ── main render ───────────────────────────────────────────────────
    function render(data) {
        applyBranding(data.branding);

        const categories    = sortGroups(data.categories);
        const serviceGroups = sortGroups(data.service_groups);

        const totalDevices  = categories.reduce((n, c) => n + c.items.length, 0);
        const totalServices = serviceGroups.reduce((n, c) => n + c.items.length, 0);
        const hasItems = totalDevices + totalServices > 0;

        // Overall banner
        els.banner.className = `overall-banner ${data.overall}`;
        els.banner.querySelector('.label').textContent = bannerLabel(data.overall, hasItems);
        els.generatedAt.textContent = `Stand: ${fmtTimeShort(data.generated_at)}`;

        // Warning banner (overseer down)
        els.warningSlot.innerHTML = renderWarningBanner(data);

        // Devices — completely hidden in standalone mode (no overseer at all).
        const standalone = !data.overseer.configured;
        document.body.classList.toggle('standalone', standalone);
        els.devicesSection.style.display = standalone ? 'none' : '';

        if (!standalone) {
            els.devicesCount.textContent = totalDevices === 0
                ? ''
                : `${totalDevices} Gerät${totalDevices === 1 ? '' : 'e'}`;

            if (totalDevices === 0) {
                els.devicesContent.innerHTML = `
                    <div class="empty-state">
                        <p>Keine Geräte sichtbar.</p>
                        <p>Sobald sich Clients beim Overseer verbinden, erscheinen sie hier.</p>
                    </div>`;
            } else {
                els.devicesContent.innerHTML =
                    categories.map(g => renderCategory(g, 'device')).join('');
            }
        }

        // Services
        els.servicesCount.textContent = totalServices === 0
            ? ''
            : `${totalServices} Service${totalServices === 1 ? '' : 's'}`;

        if (totalServices === 0) {
            els.servicesContent.innerHTML = `
                <div class="empty-state">
                    <p>Keine Services konfiguriert.</p>
                    <p>Über <a href="/manage" style="color:var(--c-purple);">Manage</a> kannst du Services hinzufügen.</p>
                </div>`;
        } else {
            els.servicesContent.innerHTML =
                serviceGroups.map(g => renderCategory(g, 'service')).join('');
        }
    }

    function renderError(err) {
        els.banner.className = 'overall-banner red';
        els.banner.querySelector('.label').textContent = 'Status-Server nicht erreichbar';
        els.generatedAt.textContent = '';
        const msg = `<div class="empty-state"><p>Fehler beim Laden: ${escapeHtml(err.message || err)}</p></div>`;
        els.devicesContent.innerHTML = msg;
        els.servicesContent.innerHTML = msg;
    }

    // ── poll loop ─────────────────────────────────────────────────────
    async function tick() {
        try {
            const r = await fetch('/api/public/status', { cache: 'no-store' });
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            const data = await r.json();
            render(data);
        } catch (err) {
            console.error('[m-status] fetch failed', err);
            renderError(err);
        }
    }

    tick();
    setInterval(tick, POLL_MS);

    // Refresh immediately when tab regains focus
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) tick();
    });
})();
