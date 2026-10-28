// Shopping list: sends the page's forms in the background and swaps in the list the server returns,
// and checks every few seconds whether someone else changed the list. Without this script every form
// still works as a normal post.
(() => {
  const list = document.getElementById('list');
  const addForm = document.querySelector('form[data-add]');
  const POLL_MS = 4000;
  let rev = list.querySelector('[data-rev]')?.dataset.rev;
  let pending = 0;  // changes in flight: a poll must not swap in a list from before them

  function swap(html) {
    list.innerHTML = html;
    rev = list.querySelector('[data-rev]')?.dataset.rev;
  }

  async function send(form) {
    pending++;
    try {
      const r = await fetch(form.action, {
        method: 'POST', body: new FormData(form), headers: { 'X-Requested-With': 'fetch' },
      });
      // A redirect elsewhere (an expired Authentik login) or a server error: let the browser do it the normal way.
      if (r.redirected || (!r.ok && r.status !== 422)) throw new Error(String(r.status));
      swap(await r.text());
      return r.ok;
    } finally {
      pending--;
    }
  }

  document.addEventListener('submit', async (e) => {
    const form = e.target;
    if (!form.matches('form[data-live]')) return;
    e.preventDefault();
    // Feels instant in the shop: tick the row before the server answers.
    if (form.matches('[data-toggle]')) form.closest('li')?.classList.toggle('done');
    try {
      const ok = await send(form);
      if (form === addForm && ok) {
        form.reset();
        form.elements.text.focus();
      }
    } catch (err) {
      form.submit();
    }
  });

  async function poll() {
    if (document.hidden || pending) return;
    try {
      const r = await fetch('/shopping/rev', { headers: { 'X-Requested-With': 'fetch' } });
      if (!r.ok || r.redirected) return;
      const { rev: now } = await r.json();
      if (String(now) === String(rev) || pending) return;
      const page = await fetch('/shopping/list', { headers: { 'X-Requested-With': 'fetch' } });
      if (page.ok && !page.redirected && !pending) swap(await page.text());
    } catch (err) { /* offline for a moment in the shop: try again next time */ }
  }

  setInterval(poll, POLL_MS);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
})();
