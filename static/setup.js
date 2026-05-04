/* ============================================================
 *  M-STATUS · setup wizard
 *  Step 1: Overseer (optional, can skip) — host + port + key
 *  Step 2: Admin account
 *  Step 3: Subtitle + finish
 * ============================================================ */
(() => {
    const state = {
        step: 1,
        overseerOk: false,    // /test passed
        skipped: false,
    };

    const $ = (id) => document.getElementById(id);
    const sections = Array.from(document.querySelectorAll('.step-section'));
    const dots     = Array.from(document.querySelectorAll('.step-dot'));

    function showStep(n) {
        state.step = n;
        sections.forEach(s => {
            s.style.display = (Number(s.dataset.step) === n) ? '' : 'none';
        });
        dots.forEach(d => {
            d.classList.toggle('active', Number(d.dataset.step) <= n);
        });
        clearMessages();
    }

    function clearMessages() {
        ['test-error', 'test-success', 'admin-error', 'finish-error']
            .forEach(id => { const el = $(id); if (el) el.textContent = ''; });
    }

    function showError(id, msg) {
        const el = $(id);
        if (!el) return;
        el.textContent = msg;
    }

    // ── step 1 — overseer ──────────────────────────────────────────────
    const hostInput = $('overseer-host');
    const portInput = $('overseer-port');
    const keyInput  = $('api-key');
    const next1     = $('next-1');

    function refreshNext1() {
        next1.disabled = !(state.overseerOk || state.skipped);
    }

    [hostInput, portInput, keyInput].forEach(inp => {
        inp.addEventListener('input', () => {
            state.overseerOk = false;
            state.skipped = false;
            $('test-success').textContent = '';
            refreshNext1();
        });
    });

    $('test-btn').addEventListener('click', async () => {
        clearMessages();
        const host = hostInput.value.trim();
        const port = parseInt(portInput.value, 10);
        const key  = keyInput.value.trim();
        if (!host || !port || !key) {
            showError('test-error', 'Bitte Host, Port und API-Key eintragen.');
            return;
        }
        $('test-btn').disabled = true;
        $('test-btn').textContent = 'Teste…';
        try {
            const r = await fetch('/api/setup/test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    overseer_host: host,
                    overseer_port: port,
                    api_key: key,
                }),
            });
            const data = await r.json();
            if (data.ok) {
                state.overseerOk = true;
                state.skipped = false;
                $('test-success').textContent = '✓ Verbindung erfolgreich.';
                refreshNext1();
            } else {
                state.overseerOk = false;
                showError('test-error', data.message || 'Test fehlgeschlagen.');
                refreshNext1();
            }
        } catch (e) {
            showError('test-error', `Fehler: ${e.message}`);
        } finally {
            $('test-btn').disabled = false;
            $('test-btn').textContent = 'Verbindung testen';
        }
    });

    $('skip-overseer').addEventListener('click', () => {
        hostInput.value = '';
        portInput.value = '';
        keyInput.value  = '';
        state.overseerOk = false;
        state.skipped = true;
        $('test-success').textContent = '';
        $('test-error').textContent   = '';
        refreshNext1();
        showStep(2);
    });

    next1.addEventListener('click', () => {
        if (next1.disabled) return;
        showStep(2);
    });

    // ── step 2 — admin ─────────────────────────────────────────────────
    $('back-2').addEventListener('click', () => showStep(1));

    $('next-2').addEventListener('click', () => {
        clearMessages();
        const u  = $('admin-user').value.trim();
        const p  = $('admin-pass').value;
        const p2 = $('admin-pass2').value;
        if (!u) { showError('admin-error', 'Benutzername darf nicht leer sein.'); return; }
        if (p.length < 8) { showError('admin-error', 'Passwort muss mindestens 8 Zeichen lang sein.'); return; }
        if (p !== p2) { showError('admin-error', 'Passwörter stimmen nicht überein.'); return; }
        showStep(3);
    });

    // ── step 3 — branding + finish ─────────────────────────────────────
    $('back-3').addEventListener('click', () => showStep(2));

    $('finish-btn').addEventListener('click', async () => {
        clearMessages();
        const btn = $('finish-btn');
        btn.disabled = true;
        btn.textContent = 'Speichere…';

        const payload = {
            overseer_host: state.skipped ? '' : hostInput.value.trim(),
            overseer_port: state.skipped ? null : (parseInt(portInput.value, 10) || null),
            api_key:       state.skipped ? '' : keyInput.value.trim(),
            admin_user:    $('admin-user').value.trim(),
            admin_pass:    $('admin-pass').value,
        };
        const st = $('page-subtitle').value.trim();
        if (st) payload.page_subtitle = st;

        try {
            const r = await fetch('/api/setup/complete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await r.json().catch(() => ({}));
            if (r.ok && data.ok) {
                btn.textContent = '✓ Fertig — leite weiter…';
                setTimeout(() => { window.location.href = '/'; }, 700);
                return;
            }
            const msg = data.detail || data.message || `HTTP ${r.status}`;
            showError('finish-error', msg);
        } catch (e) {
            showError('finish-error', `Fehler: ${e.message}`);
        } finally {
            if ($('finish-error').textContent) {
                btn.disabled = false;
                btn.textContent = 'Setup abschließen';
            }
        }
    });

    showStep(1);
    refreshNext1();
})();
