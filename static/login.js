/* ============================================================
 *  M-STATUS · admin login
 * ============================================================ */
(() => {
    const form    = document.getElementById('login-form');
    const userIn  = document.getElementById('username');
    const passIn  = document.getElementById('password');
    const errorEl = document.getElementById('login-error');
    const submit  = document.getElementById('submit-btn');

    function showError(msg) {
        errorEl.textContent = msg;
    }

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        showError('');

        const username = userIn.value.trim();
        const password = passIn.value;
        if (!username || !password) {
            showError('Benutzername und Passwort dürfen nicht leer sein.');
            return;
        }

        submit.disabled = true;
        const originalLabel = submit.innerHTML;
        submit.innerHTML = 'Melde an…';

        try {
            const r = await fetch('/api/admin/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, password }),
            });
            const data = await r.json().catch(() => ({}));
            if (r.ok && data.ok) {
                window.location.href = '/manage';
                return;
            }
            const msg = data.detail || data.message || 'Anmeldung fehlgeschlagen.';
            showError(msg);
            passIn.value = '';
            passIn.focus();
        } catch (err) {
            showError(`Netzwerkfehler: ${err.message}`);
        } finally {
            submit.disabled = false;
            submit.innerHTML = originalLabel;
        }
    });

    // Auto-focus first empty field
    if (!userIn.value) userIn.focus();
    else passIn.focus();
})();
