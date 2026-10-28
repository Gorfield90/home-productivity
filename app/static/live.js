// Live pages (shopping list, to-do lists): sends the page's forms in the background and swaps in the
// fragment the server returns, and checks every few seconds whether anyone changed anything. The part
// that updates is #live, whose data-src is the URL of its fragment. Without this script every form
// still works as a normal post.
(() => {
  const box = document.getElementById('live');
  if (!box) return;
  const addForm = document.querySelector('form[data-add]');
  const POLL_MS = 4000;
  const FETCH = { 'X-Requested-With': 'fetch' };
  let rev = box.querySelector('[data-rev]')?.dataset.rev;
  let pending = 0;  // changes in flight: a poll must not swap in a page from before them

  function swap(html) {
    box.innerHTML = html;
    rev = box.querySelector('[data-rev]')?.dataset.rev;
  }

  async function send(form) {
    pending++;
    try {
      const headers = { ...FETCH };
      if (form.dataset.from) headers['X-From'] = form.dataset.from;
      const r = await fetch(form.action, { method: 'POST', body: new FormData(form), headers });
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
    // Feels instant: tick the row before the server answers.
    if (form.matches('[data-toggle]')) form.closest('li')?.classList.toggle('done');
    try {
      const ok = await send(form);
      if (form === addForm && ok) {
        // Keep the chosen aisle or assignee for the next one; clear what was typed and the date.
        form.elements[0].value = '';
        form.querySelectorAll('input[type=date]').forEach((i) => { i.value = ''; });
        form.elements[0].focus();
      }
    } catch (err) {
      form.submit();
    }
  });

  async function poll() {
    if (document.hidden || pending) return;
    try {
      const r = await fetch('/rev', { headers: FETCH });
      if (!r.ok || r.redirected) return;
      const { rev: now } = await r.json();
      if (String(now) === String(rev) || pending) return;
      const page = await fetch(box.dataset.src, { headers: FETCH });
      if (page.ok && !page.redirected && !pending) swap(await page.text());
    } catch (err) { /* offline for a moment: try again next time */ }
  }

  setInterval(poll, POLL_MS);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
})();
