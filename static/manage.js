/* ============================================================
 *  M-STATUS · admin manage panel
 *  Tabs: Devices · Categories · Services · Settings
 * ============================================================ */
(() => {

    // ── tiny utilities ────────────────────────────────────────────────
    const $ = (id) => document.getElementById(id);
    const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
    const escapeHtml = (s) => String(s ?? '').replace(/[&<>"']/g, m => (
        { '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[m]
    ));
    const fmtRelative = (ts) => {
        if (!ts) return '—';
        const diff = (Date.now() / 1000) - ts;
        if (diff < 60)     return 'gerade eben';
        if (diff < 3600)   return `vor ${Math.floor(diff / 60)} Min`;
        if (diff < 86_400) return `vor ${Math.floor(diff / 3600)} h`;
        return `vor ${Math.floor(diff / 86400)} Tagen`;
    };

    // ── API wrapper ───────────────────────────────────────────────────
    async function api(method, path, body) {
        const opts = { method, headers: {}, credentials: 'same-origin' };
        if (body !== undefined) {
            opts.headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(body);
        }
        const r = await fetch(path, opts);

        if (r.status === 401) {
            window.location.href = '/login';
            throw new Error('Session expired');
        }

        let data = null;
        try { data = await r.json(); } catch { /* empty body */ }

        if (!r.ok) {
            const msg = (data && (data.detail || data.message)) || `HTTP ${r.status}`;
            const err = new Error(msg);
            err.status = r.status;
            throw err;
        }
        return data;
    }

    // ── Toasts ────────────────────────────────────────────────────────
    const toastRoot = $('toast-container');
    const Toast = {
        show(kind, title, body) {
            const el = document.createElement('div');
            el.className = `toast ${kind}`;
            el.innerHTML = `
                <div class="t-title">${escapeHtml(title)}</div>
                ${body ? `<div class="t-msg">${escapeHtml(body)}</div>` : ''}
            `;
            toastRoot.appendChild(el);
            setTimeout(() => {
                el.style.transition = 'opacity 0.25s, transform 0.25s';
                el.style.opacity = '0';
                el.style.transform = 'translateX(20px)';
                setTimeout(() => el.remove(), 260);
            }, 3500);
        },
        success(t, b) { this.show('success', t, b); },
        error  (t, b) { this.show('error',   t, b); },
        warn   (t, b) { this.show('warning', t, b); },
    };

    // ── Modal helpers ─────────────────────────────────────────────────
    function openModal(id) {
        const m = $(id);
        if (!m) return;
        m.classList.add('open');
        setTimeout(() => {
            const inp = m.querySelector('input:not([type=hidden]), select, textarea');
            if (inp) inp.focus();
        }, 60);
    }
    function closeModal(id) {
        const m = $(id);
        if (m) m.classList.remove('open');
    }

    document.addEventListener('click', (e) => {
        if (e.target.matches('[data-close]') || e.target.classList.contains('modal-backdrop')) {
            const m = e.target.closest('.modal-backdrop');
            if (m) m.classList.remove('open');
        }
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') $$('.modal-backdrop.open').forEach(m => m.classList.remove('open'));
    });

    function confirmAsync(text, btnLabel = 'Bestätigen') {
        return new Promise(resolve => {
            const modal = $('confirm-modal');
            $('cf-text').textContent = text;
            const btn = $('cf-confirm');
            btn.textContent = btnLabel;

            let settled = false;
            const observer = new MutationObserver(() => {
                if (!modal.classList.contains('open') && !settled) {
                    settled = true;
                    observer.disconnect();
                    btn.removeEventListener('click', onConfirm);
                    resolve(false);
                }
            });
            const onConfirm = () => {
                if (settled) return;
                settled = true;
                observer.disconnect();
                btn.removeEventListener('click', onConfirm);
                modal.classList.remove('open');
                resolve(true);
            };
            btn.addEventListener('click', onConfirm);
            observer.observe(modal, { attributes: true, attributeFilter: ['class'] });
            openModal('confirm-modal');
        });
    }

    // ── Drag & drop helper for tables ─────────────────────────────────
    //
    // Wires up native HTML5 dnd on every <tr> in `tbody`. After a drop,
    // calls `onCommit(orderedIds)` with the new order of `data-id` values.
    function makeReorderable(tbody, onCommit) {
        let dragSrc = null;

        tbody.querySelectorAll('tr').forEach(tr => {
            tr.setAttribute('draggable', 'true');

            tr.addEventListener('dragstart', (e) => {
                dragSrc = tr;
                tr.classList.add('dragging');
                e.dataTransfer.effectAllowed = 'move';
                // Required for Firefox to start a drag.
                try { e.dataTransfer.setData('text/plain', tr.dataset.id || ''); } catch {}
            });
            tr.addEventListener('dragend', () => {
                tr.classList.remove('dragging');
                tbody.querySelectorAll('tr').forEach(r => r.classList.remove('drag-over'));
                dragSrc = null;
            });
            tr.addEventListener('dragover', (e) => {
                if (!dragSrc || dragSrc === tr) return;
                e.preventDefault();
                e.dataTransfer.dropEffect = 'move';
                tr.classList.add('drag-over');
            });
            tr.addEventListener('dragleave', () => {
                tr.classList.remove('drag-over');
            });
            tr.addEventListener('drop', (e) => {
                e.preventDefault();
                tr.classList.remove('drag-over');
                if (!dragSrc || dragSrc === tr) return;

                // Insert source before target if it currently sits later in
                // the DOM, otherwise after — feels natural either direction.
                const all = Array.from(tbody.children);
                const srcIdx = all.indexOf(dragSrc);
                const tgtIdx = all.indexOf(tr);
                if (srcIdx < tgtIdx) {
                    tr.parentNode.insertBefore(dragSrc, tr.nextSibling);
                } else {
                    tr.parentNode.insertBefore(dragSrc, tr);
                }

                const newOrder = Array.from(tbody.querySelectorAll('tr'))
                    .map(r => r.dataset.id)
                    .filter(Boolean);
                onCommit(newOrder).catch(err => Toast.error('Sortierung speichern fehlgeschlagen', err.message));
            });
        });
    }

    // ── Tab switching ─────────────────────────────────────────────────
    function switchTab(name) {
        $$('.nav-link[data-tab]').forEach(a => {
            a.classList.toggle('active', a.dataset.tab === name);
        });
        $$('.tab-section').forEach(s => {
            s.classList.toggle('active', s.dataset.tab === name);
        });
        if (name === 'devices')    loadDevices();
        if (name === 'categories') loadCategories();
        if (name === 'services')   loadServices();
        if (name === 'settings')   loadSettings();
        history.replaceState(null, '', '#' + name);
    }

    $$('.nav-link[data-tab]').forEach(a => {
        a.addEventListener('click', (e) => { e.preventDefault(); switchTab(a.dataset.tab); });
    });

    $('logout-chip').addEventListener('click', async () => {
        try { await api('POST', '/api/admin/logout'); } catch {}
        window.location.href = '/login';
    });

    // ════════════════════════════════════════════════════════════════
    //  STATE LABELS (matches status.py colour_from_fails)
    // ════════════════════════════════════════════════════════════════

    const COLOUR_LABEL = {
        green:  'Online',
        orange: 'Probleme',
        red:    'Fehlerhaft',
        grey:   'Keine Daten',
    };

    // ════════════════════════════════════════════════════════════════
    //  DEVICES
    // ════════════════════════════════════════════════════════════════

    let _categoriesCache = [];

    async function loadDevices() {
        const wrap = $('devices-table-wrap');
        wrap.innerHTML = `<div class="loading">Lade…</div>`;
        try {
            const [d, c] = await Promise.all([
                api('GET', '/api/admin/devices'),
                api('GET', '/api/admin/categories'),
            ]);
            _categoriesCache = c.categories;
            renderDevices(d.devices);
            const onlineCount = d.devices.filter(x => x.online).length;
            $('devices-summary').textContent =
                `${d.devices.length} Gerät${d.devices.length === 1 ? '' : 'e'} · ` +
                `${onlineCount} online · ` +
                `${d.devices.filter(x => x.hidden).length} ausgeblendet`;
        } catch (e) {
            wrap.innerHTML = `<div class="error-msg">Fehler: ${escapeHtml(e.message)}</div>`;
        }
    }

    function deviceColour(d) {
        if (d.deleted) return 'grey';
        const fails = d.consecutive_fail_runs || 0;
        if (d.last_check == null && fails === 0) return 'grey';
        if (fails === 0) return 'green';
        if (fails < 4)   return 'orange';
        return 'red';
    }

    function deviceLabel(d) {
        const colour = deviceColour(d);
        if (colour === 'grey')   return 'Keine Daten';
        if (colour === 'green')  return 'Online';
        if (colour === 'orange') return 'Probleme';
        return (d.consecutive_fail_runs >= 8) ? 'Offline' : 'Fehlerhaft';
    }

    function renderDevices(devices) {
        const wrap = $('devices-table-wrap');
        if (devices.length === 0) {
            wrap.innerHTML = `
                <div class="empty-state">
                    <p>Noch keine Geräte vom Overseer empfangen.</p>
                    <p class="hint">Sobald sich Clients verbinden, erscheinen sie hier.</p>
                </div>`;
            return;
        }

        const rows = devices.map(d => {
            const colour = deviceColour(d);
            const label  = deviceLabel(d);
            const dot = `<span class="status-dot dot-${colour}"></span>`;
            const cat = d.category_name
                ? `<span class="chip">${escapeHtml(d.category_name)}</span>`
                : `<span class="chip chip-muted">Allgemein</span>`;
            const hiddenChip = d.hidden ? `<span class="chip chip-muted">Hidden</span>` : '';
            return `
                <tr data-id="${escapeHtml(d.client_id)}">
                    <td><span class="drag-handle" title="Ziehen zum Sortieren">⋮⋮</span></td>
                    <td>${dot}</td>
                    <td class="cell-name">
                        <div class="cell-name-main">${escapeHtml(d.name)}</div>
                        <div class="cell-name-sub">${escapeHtml(d.hostname || '')} · ${escapeHtml(d.ip || '')}</div>
                    </td>
                    <td><span class="text-muted">${escapeHtml(label)}</span></td>
                    <td>${cat} ${hiddenChip}</td>
                    <td class="text-muted">${fmtRelative(d.last_check)}</td>
                    <td class="cell-actions">
                        <button class="btn btn-ghost btn-xs" data-act="edit">Bearbeiten</button>
                        <button class="btn btn-ghost btn-xs danger" data-act="delete">Entfernen</button>
                    </td>
                </tr>`;
        }).join('');

        wrap.innerHTML = `
            <table class="data-table">
                <thead>
                    <tr>
                        <th style="width:30px;"></th>
                        <th style="width:40px;"></th>
                        <th>Name</th>
                        <th>Status</th>
                        <th>Kategorie</th>
                        <th>Letzter Check</th>
                        <th style="width:160px;"></th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        `;

        const tbody = wrap.querySelector('tbody');
        makeReorderable(tbody, async (orderedIds) => {
            await api('POST', '/api/admin/devices/reorder', { order: orderedIds });
            Toast.success('Reihenfolge gespeichert');
        });

        wrap.querySelectorAll('button[data-act]').forEach(btn => {
            btn.addEventListener('click', () => {
                const cid = btn.closest('tr').dataset.id;
                const dev = devices.find(x => x.client_id === cid);
                if (!dev) return;
                if (btn.dataset.act === 'edit')   openDeviceEdit(dev);
                if (btn.dataset.act === 'delete') deleteDevice(dev);
            });
        });
    }

    function openDeviceEdit(dev) {
        $('dm-name').value   = dev.name || '';
        $('dm-hidden').checked = !!dev.hidden;

        const sel = $('dm-category');
        sel.innerHTML = `<option value="0">— Allgemein —</option>` +
            _categoriesCache.map(c =>
                `<option value="${c.id}" ${dev.category_id === c.id ? 'selected' : ''}>
                    ${escapeHtml(c.name)}
                </option>`).join('');

        $('dm-save').onclick = async () => {
            const body = {
                name: $('dm-name').value.trim(),
                hidden: $('dm-hidden').checked,
                category_id: parseInt($('dm-category').value, 10) || 0,
            };
            try {
                await api('PATCH', `/api/admin/devices/${encodeURIComponent(dev.client_id)}`, body);
                Toast.success('Gerät gespeichert');
                closeModal('device-edit-modal');
                loadDevices();
            } catch (e) {
                Toast.error('Speichern fehlgeschlagen', e.message);
            }
        };
        openModal('device-edit-modal');
    }

    async function deleteDevice(dev) {
        const ok = await confirmAsync(
            `„${dev.name}" wirklich aus M-STATUS entfernen?  ` +
            `Die History wird behalten. Falls das Gerät neu mit dem Overseer verbindet, ` +
            `taucht es wieder auf.`,
            'Entfernen'
        );
        if (!ok) return;
        try {
            await api('DELETE', `/api/admin/devices/${encodeURIComponent(dev.client_id)}`);
            Toast.success('Gerät entfernt');
            loadDevices();
        } catch (e) {
            Toast.error('Löschen fehlgeschlagen', e.message);
        }
    }

    $('refresh-devices').addEventListener('click', loadDevices);

    $('sync-overseer-btn').addEventListener('click', async () => {
        const btn = $('sync-overseer-btn');
        btn.disabled = true;
        btn.textContent = 'Synchronisiere…';
        try {
            const r = await api('POST', '/api/admin/overseer/sync-now');
            Toast.success('Inventar synchronisiert', `${r.seen} gesehen, ${r.removed} entfernt`);
            loadDevices();
        } catch (e) {
            Toast.error('Sync fehlgeschlagen', e.message);
        } finally {
            btn.disabled = false;
            btn.textContent = 'Inventar synchronisieren';
        }
    });

    // ════════════════════════════════════════════════════════════════
    //  CATEGORIES
    // ════════════════════════════════════════════════════════════════

    async function loadCategories() {
        const wrap = $('categories-table-wrap');
        wrap.innerHTML = `<div class="loading">Lade…</div>`;
        try {
            const c = await api('GET', '/api/admin/categories');
            _categoriesCache = c.categories;
            renderCategories(c.categories);
        } catch (e) {
            wrap.innerHTML = `<div class="error-msg">Fehler: ${escapeHtml(e.message)}</div>`;
        }
    }

    function renderCategories(cats) {
        const wrap = $('categories-table-wrap');
        if (cats.length === 0) {
            wrap.innerHTML = `
                <div class="empty-state">
                    <p>Noch keine Kategorien.</p>
                    <p class="hint">Lege z. B. „Server", „LXCs" oder „Network" an.</p>
                </div>`;
            return;
        }
        const rows = cats.map(c => `
            <tr data-id="${c.id}">
                <td><span class="chip">${escapeHtml(c.name)}</span></td>
                <td class="text-muted">Sortier-Index: ${c.sort_order}</td>
                <td class="cell-actions">
                    <button class="btn btn-ghost btn-xs" data-act="edit">Bearbeiten</button>
                    <button class="btn btn-ghost btn-xs danger" data-act="delete">Löschen</button>
                </td>
            </tr>`).join('');
        wrap.innerHTML = `
            <table class="data-table">
                <thead><tr><th>Name</th><th>Reihenfolge</th><th style="width:160px;"></th></tr></thead>
                <tbody>${rows}</tbody>
            </table>`;

        wrap.querySelectorAll('button[data-act]').forEach(btn => {
            btn.addEventListener('click', () => {
                const id = parseInt(btn.closest('tr').dataset.id, 10);
                const cat = cats.find(x => x.id === id);
                if (!cat) return;
                if (btn.dataset.act === 'edit')   openCategoryModal(cat);
                if (btn.dataset.act === 'delete') deleteCategory(cat);
            });
        });
    }

    function openCategoryModal(cat) {
        const isEdit = !!cat;
        $('cm-name').value  = cat ? cat.name : '';
        $('cm-order').value = cat ? cat.sort_order : 0;

        $('cm-save').onclick = async () => {
            const body = {
                name: $('cm-name').value.trim(),
                sort_order: parseInt($('cm-order').value, 10) || 0,
            };
            if (!body.name) { Toast.warn('Name fehlt'); return; }
            try {
                if (isEdit) await api('PATCH', `/api/admin/categories/${cat.id}`, body);
                else        await api('POST',  '/api/admin/categories', body);
                Toast.success(isEdit ? 'Kategorie aktualisiert' : 'Kategorie angelegt');
                closeModal('category-modal');
                loadCategories();
            } catch (e) {
                Toast.error('Fehler', e.message);
            }
        };
        openModal('category-modal');
    }

    async function deleteCategory(cat) {
        const ok = await confirmAsync(
            `Kategorie „${cat.name}" löschen?  ` +
            `Alle zugehörigen Geräte und Services landen wieder unter „Allgemein".`,
            'Löschen'
        );
        if (!ok) return;
        try {
            await api('DELETE', `/api/admin/categories/${cat.id}`);
            Toast.success('Kategorie gelöscht');
            loadCategories();
        } catch (e) {
            Toast.error('Löschen fehlgeschlagen', e.message);
        }
    }

    $('new-category-btn').addEventListener('click', () => openCategoryModal(null));

    // ════════════════════════════════════════════════════════════════
    //  SERVICES
    // ════════════════════════════════════════════════════════════════

    async function loadServices() {
        const wrap = $('services-table-wrap');
        wrap.innerHTML = `<div class="loading">Lade…</div>`;
        try {
            // Make sure we have categories cached for the modal dropdown.
            const [s, c] = await Promise.all([
                api('GET', '/api/admin/services'),
                api('GET', '/api/admin/categories'),
            ]);
            _categoriesCache = c.categories;
            renderServices(s.services);
        } catch (e) {
            wrap.innerHTML = `<div class="error-msg">Fehler: ${escapeHtml(e.message)}</div>`;
        }
    }

    function serviceColour(s) {
        const fails = s.consecutive_fail_runs || 0;
        if (s.last_check == null) return 'grey';
        if (fails === 0) return 'green';
        if (fails < 4)   return 'orange';
        return 'red';
    }

    function serviceLabel(s) {
        const colour = serviceColour(s);
        if (colour === 'grey')   return 'Keine Daten';
        if (colour === 'green')  return 'Online';
        if (colour === 'orange') return 'Probleme';
        return (s.consecutive_fail_runs >= 8) ? 'Offline' : 'Fehlerhaft';
    }

    function renderServices(services) {
        const wrap = $('services-table-wrap');
        if (services.length === 0) {
            wrap.innerHTML = `
                <div class="empty-state">
                    <p>Noch keine Services.</p>
                    <p class="hint">Web-Endpoints und lokale Dienste landen hier.</p>
                </div>`;
            return;
        }

        const rows = services.map(s => {
            const colour = serviceColour(s);
            const label  = serviceLabel(s);
            const dot = `<span class="status-dot dot-${colour}"></span>`;
            const kind = s.is_local
                ? `<span class="chip">Lokal</span>`
                : `<span class="chip chip-muted">Extern</span>`;
            const cat = s.category_name
                ? `<span class="chip">${escapeHtml(s.category_name)}</span>`
                : `<span class="chip chip-muted">Allgemein</span>`;
            const internalInfo = s.is_local
                ? ` · ${escapeHtml(s.internal_host || '')}:${s.internal_port || ''}`
                : '';
            const fails = s.consecutive_fail_runs > 0
                ? `<span class="text-muted"> · ${s.consecutive_fail_runs}× in Folge</span>`
                : '';
            return `
                <tr data-id="${s.id}">
                    <td><span class="drag-handle" title="Ziehen zum Sortieren">⋮⋮</span></td>
                    <td>${dot}</td>
                    <td class="cell-name">
                        <div class="cell-name-main">${escapeHtml(s.name)}</div>
                        <div class="cell-name-sub">${escapeHtml(s.external_url)}${internalInfo}</div>
                    </td>
                    <td><span class="text-muted">${escapeHtml(label)}</span></td>
                    <td>${kind} ${cat}</td>
                    <td class="text-muted">
                        ${fmtRelative(s.last_check)}${fails}
                    </td>
                    <td class="cell-actions">
                        <button class="btn btn-ghost btn-xs" data-act="edit">Bearbeiten</button>
                        <button class="btn btn-ghost btn-xs danger" data-act="delete">Löschen</button>
                    </td>
                </tr>`;
        }).join('');

        wrap.innerHTML = `
            <table class="data-table">
                <thead>
                    <tr>
                        <th style="width:30px;"></th>
                        <th style="width:40px;"></th>
                        <th>Name</th>
                        <th>Status</th>
                        <th>Typ / Kategorie</th>
                        <th>Letzter Check</th>
                        <th style="width:160px;"></th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>`;

        const tbody = wrap.querySelector('tbody');
        makeReorderable(tbody, async (orderedIds) => {
            const order = orderedIds.map(x => parseInt(x, 10)).filter(Number.isFinite);
            await api('POST', '/api/admin/services/reorder', { order });
            Toast.success('Reihenfolge gespeichert');
        });

        wrap.querySelectorAll('button[data-act]').forEach(btn => {
            btn.addEventListener('click', () => {
                const id = parseInt(btn.closest('tr').dataset.id, 10);
                const svc = services.find(x => x.id === id);
                if (!svc) return;
                if (btn.dataset.act === 'edit')   openServiceModal(svc);
                if (btn.dataset.act === 'delete') deleteService(svc);
            });
        });
    }

    function openServiceModal(svc) {
        const isEdit = !!svc;
        $('sm-name').value     = svc ? svc.name : '';
        $('sm-url').value      = svc ? svc.external_url : '';
        $('sm-is-local').checked = svc ? !!svc.is_local : false;
        $('sm-host').value     = svc ? (svc.internal_host || '') : '';
        $('sm-port').value     = svc ? (svc.internal_port || '') : '';

        const catSel = $('sm-category');
        catSel.innerHTML = `<option value="0">— Allgemein —</option>` +
            _categoriesCache.map(c =>
                `<option value="${c.id}" ${svc && svc.category_id === c.id ? 'selected' : ''}>
                    ${escapeHtml(c.name)}
                </option>`).join('');

        toggleLocalFields();

        $('sm-save').onclick = async () => {
            const body = {
                name: $('sm-name').value.trim(),
                external_url: $('sm-url').value.trim(),
                is_local: $('sm-is-local').checked,
                internal_host: $('sm-host').value.trim() || null,
                internal_port: parseInt($('sm-port').value, 10) || null,
                category_id: parseInt($('sm-category').value, 10) || 0,
                sort_order: svc ? (svc.sort_order || 0) : 0,
            };
            if (!body.name)         { Toast.warn('Name fehlt'); return; }
            if (!body.external_url) { Toast.warn('Externe URL fehlt'); return; }
            if (body.is_local) {
                if (!body.internal_host || !body.internal_port) {
                    Toast.warn('Lokale Services brauchen Host + Port'); return;
                }
            }
            try {
                if (isEdit) await api('PATCH', `/api/admin/services/${svc.id}`, body);
                else        await api('POST',  '/api/admin/services', body);
                Toast.success(isEdit ? 'Service aktualisiert' : 'Service angelegt');
                closeModal('service-modal');
                loadServices();
            } catch (e) {
                Toast.error('Fehler', e.message);
            }
        };
        openModal('service-modal');
    }

    function toggleLocalFields() {
        $('sm-local-fields').style.display = $('sm-is-local').checked ? '' : 'none';
    }
    $('sm-is-local').addEventListener('change', toggleLocalFields);

    async function deleteService(svc) {
        const ok = await confirmAsync(`Service „${svc.name}" löschen?  Die History geht verloren.`, 'Löschen');
        if (!ok) return;
        try {
            await api('DELETE', `/api/admin/services/${svc.id}`);
            Toast.success('Service gelöscht');
            loadServices();
        } catch (e) {
            Toast.error('Löschen fehlgeschlagen', e.message);
        }
    }

    $('new-service-btn').addEventListener('click', () => openServiceModal(null));

    $('check-now-btn').addEventListener('click', async () => {
        try {
            await api('POST', '/api/admin/services/check-now');
            Toast.success('Check ausgelöst', 'Ergebnisse erscheinen in wenigen Sekunden.');
            setTimeout(loadServices, 3000);
        } catch (e) {
            Toast.error('Fehler', e.message);
        }
    });

    // ════════════════════════════════════════════════════════════════
    //  SETTINGS
    // ════════════════════════════════════════════════════════════════

    async function loadSettings() {
        try {
            const [ovs, br] = await Promise.all([
                api('GET', '/api/admin/overseer'),
                api('GET', '/api/admin/branding'),
            ]);
            $('cfg-overseer-host').value = ovs.overseer_host || '';
            $('cfg-overseer-port').value = ovs.overseer_port || '';
            $('cfg-api-key').value = '';
            $('cfg-api-key').placeholder = ovs.api_key_set
                ? '(gesetzt — leer lassen, um nicht zu ändern)'
                : '(nicht gesetzt)';

            const conn = ovs.connection || {};
            const stEl = $('overseer-status');
            if (!ovs.overseer_host || !ovs.overseer_port || !ovs.api_key_set) {
                stEl.innerHTML = `<span class="chip chip-muted">Nicht konfiguriert (Standalone-Modus)</span>`;
            } else if (conn.connected) {
                stEl.innerHTML = `<span class="chip chip-green">Verbunden</span>`;
            } else if (conn.last_sync_ok) {
                stEl.innerHTML = `<span class="chip chip-orange">REST OK · WS getrennt</span>`;
            } else {
                const err = conn.last_error ? ` · ${escapeHtml(conn.last_error)}` : '';
                stEl.innerHTML = `<span class="chip chip-red">Getrennt</span><span class="text-muted">${err}</span>`;
            }

            $('cfg-subtitle').value      = br.page_subtitle || '';
            $('cfg-footer-owner').value  = br.footer_owner  || '';
            $('cfg-manage-label').value  = br.manage_label  || '';
        } catch (e) {
            Toast.error('Einstellungen laden fehlgeschlagen', e.message);
        }
    }

    $('save-overseer-btn').addEventListener('click', async () => {
        const body = {
            overseer_host: $('cfg-overseer-host').value.trim(),
            overseer_port: parseInt($('cfg-overseer-port').value, 10) || 0,
        };
        const key = $('cfg-api-key').value.trim();
        if (key) body.api_key = key;
        try {
            await api('POST', '/api/admin/overseer', body);
            Toast.success('Overseer gespeichert', 'Verbindung wird neu aufgebaut…');
            $('cfg-api-key').value = '';
            await detectStandalone();
            setTimeout(loadSettings, 1500);
        } catch (e) {
            Toast.error('Fehler', e.message);
        }
    });

    $('save-branding-btn').addEventListener('click', async () => {
        const body = {
            page_subtitle: $('cfg-subtitle').value.trim()     || null,
            footer_owner:  $('cfg-footer-owner').value.trim() || null,
            manage_label:  $('cfg-manage-label').value.trim() || null,
        };
        try {
            await api('POST', '/api/admin/branding', body);
            Toast.success('Branding gespeichert');
        } catch (e) {
            Toast.error('Fehler', e.message);
        }
    });

    $('save-pw-btn').addEventListener('click', async () => {
        const cur = $('pw-current').value;
        const nw  = $('pw-new').value;
        const nw2 = $('pw-new2').value;
        if (!cur || !nw)   { Toast.warn('Bitte alle Felder ausfüllen'); return; }
        if (nw.length < 8) { Toast.warn('Neues Passwort braucht mindestens 8 Zeichen'); return; }
        if (nw !== nw2)    { Toast.warn('Neue Passwörter stimmen nicht überein'); return; }
        try {
            await api('POST', '/api/admin/password', { current: cur, new: nw });
            Toast.success('Passwort geändert');
            $('pw-current').value = $('pw-new').value = $('pw-new2').value = '';
        } catch (e) {
            Toast.error('Fehler', e.message);
        }
    });

    // ── boot ──────────────────────────────────────────────────────────
    async function detectStandalone() {
        try {
            const ovs = await api('GET', '/api/admin/overseer');
            const isStandalone = !(ovs.overseer_host && ovs.overseer_port && ovs.api_key_set);
            document.body.classList.toggle('standalone', isStandalone);
            return isStandalone;
        } catch {
            return false;
        }
    }

    (async () => {
        const isStandalone = await detectStandalone();
        const requested = (location.hash || '').replace('#', '');
        const valid = isStandalone
            ? ['categories', 'services', 'settings']
            : ['devices', 'categories', 'services', 'settings'];
        const fallback = isStandalone ? 'services' : 'devices';
        switchTab(valid.includes(requested) ? requested : fallback);
    })();

})();
