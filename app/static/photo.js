// Photo upload: choosing a photo sends it straight away, shrunk on the phone first (a phone photo is several MB;
// this makes it a few hundred KB, plus a small thumbnail for lists). Re-drawing it also drops the photo's
// hidden details such as where it was taken. Without this script the form posts the original file.
(() => {
  const FULL = 1600, THUMB = 360;

  async function shrink(file, max, quality) {
    const img = await createImageBitmap(file);  // applies the camera's rotation
    const scale = Math.min(1, max / Math.max(img.width, img.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(img.width * scale);
    canvas.height = Math.round(img.height * scale);
    canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
    img.close?.();
    return new Promise((ok, fail) => canvas.toBlob((b) => (b ? ok(b) : fail(new Error('encode'))), 'image/jpeg', quality));
  }

  document.querySelectorAll('form[data-photo]').forEach((form) => {
    const input = form.querySelector('input[type=file]');
    const msg = form.querySelector('.photo-msg');
    const say = (text, error) => { if (msg) { msg.textContent = text; msg.classList.toggle('error', !!error); } };

    input.addEventListener('change', async () => {
      const file = input.files[0];
      if (!file) return;
      say('Adding the photo…');
      form.classList.add('busy');
      const body = new FormData();
      try {
        body.append('photo', await shrink(file, FULL, 0.82), 'photo.jpg');
        body.append('thumb', await shrink(file, THUMB, 0.75), 'thumb.jpg');
      } catch (err) {
        body.set('photo', file);  // a format this browser can't draw: send it as it is and let the server judge
        body.delete('thumb');
      }
      try {
        const r = await fetch(form.action, { method: 'POST', body });
        if (r.ok) { location.href = r.url; return; }
        const page = new DOMParser().parseFromString(await r.text(), 'text/html');
        say(page.querySelector('.flash.error')?.textContent || 'The photo could not be added. Try again.', true);
      } catch (err) {
        say('The photo could not be added. Check the connection and try again.', true);
      } finally {
        form.classList.remove('busy');
        input.value = '';
      }
    });
  });
})();
