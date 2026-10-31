(() => {
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const $ = (id) => document.getElementById(id);
  const map = L.map('map').setView([-2.5, 118], 4);
  L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
    maxZoom: 17,
    attribution: 'Map data: &copy; OpenStreetMap contributors, SRTM | Map style: &copy; <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)'
  }).addTo(map);
  let marker = null, accuracyCircle = null, watchId = null, stream = null;
  let lastAddressLookup = 0, lastAddressCoords = null;
  let selectedHistoryShareId = null;
  let lastEventId = 0;

  async function lookupAddress(latitude, longitude) {
    const now = Date.now();
    // Reverse geocoding dibatasi agar tidak membebani layanan publik.
    if (now - lastAddressLookup < 60000) return;
    if (lastAddressCoords && map.distance(lastAddressCoords, [latitude, longitude]) < 50) return;
    lastAddressLookup = now;
    lastAddressCoords = [latitude, longitude];
    $('address-name').textContent = 'Mencari detail alamat…';
    try {
      const url = `https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat=${encodeURIComponent(latitude)}&lon=${encodeURIComponent(longitude)}&zoom=18&addressdetails=1&accept-language=id`;
      const response = await fetch(url, {headers:{'Accept':'application/json'}});
      if (!response.ok) throw new Error('Layanan alamat sedang tidak tersedia');
      const data = await response.json();
      const a = data.address || {};

      // Gunakan hanya komponen alamat yang benar-benar diberikan oleh sumber peta.
      // Jangan menganggap nama jalan sebagai nama gedung. Utamakan objek bernama
      // yang benar-benar diberikan Nominatim; komponen alamat menjadi konteks/cadangan.
      const buildingName = a.building || a.amenity || a.shop || a.office || a.tourism || a.leisure;
      const areaParts = [
        a.neighbourhood || a.quarter,
        a.suburb || a.village || a.hamlet,
        a.city_district || a.district,
        a.city || a.town || a.municipality || a.county,
        a.state,
        a.postcode
      ].filter(Boolean);
      const uniqueArea = [...new Set(areaParts)];
      const road = a.road || a.pedestrian || a.residential;
      const house = a.house_number ? `${road || ''} ${a.house_number}`.trim() : road;
      const candidateName = (data.name && data.name !== road && data.name !== a.neighbourhood)
        ? data.name : buildingName;
      const contextParts = [
        house,
        ...uniqueArea
      ].filter(Boolean);
      const context = [...new Set(contextParts)].join(', ');
      let addressText;
      if (candidateName) {
        addressText = context && !context.includes(candidateName)
          ? `${candidateName} · ${context}`
          : candidateName;
      } else if (uniqueArea.length) {
        // Jika data peta hanya tahu jalan, nyatakan bahwa nama gedung belum tersedia.
        addressText = `${uniqueArea.join(', ')}${road ? ` · Jalan: ${road}` : ''}`;
      } else {
        addressText = data.display_name || 'Nama lokasi tidak ditemukan di data peta';
      }
      $('address-name').textContent = candidateName
        ? `Nama tempat dari peta: ${addressText}`
        : `Nama gedung belum tersedia di data peta: ${addressText}`;

      // Tampilkan koordinat presisi yang diterima browser, tanpa mengklaim alamat lebih akurat.
      const link = $('address-map-link');
      if (link) {
        link.href = `https://www.openstreetmap.org/?mlat=${encodeURIComponent(latitude)}&mlon=${encodeURIComponent(longitude)}#map=19/${encodeURIComponent(latitude)}/${encodeURIComponent(longitude)}`;
        link.hidden = false;
      }
    } catch (e) {
      $('address-name').textContent = 'Detail alamat belum tersedia. Koordinat GPS tetap bisa digunakan.';
    }
  }


  let lastNearbyLookup = 0, lastNearbyCoords = null;
  async function lookupNearbyPlaces(latitude, longitude) {
    const now = Date.now();
    // Overpass publik bukan layanan real-time; batasi permintaan dan tampilkan sebagai kandidat sekitar.
    if (now - lastNearbyLookup < 120000) return;
    if (lastNearbyCoords && map.distance(lastNearbyCoords, [latitude, longitude]) < 100) return;
    lastNearbyLookup = now;
    lastNearbyCoords = [latitude, longitude];
    const output = $('nearby-places');
    if (!output) return;
    output.textContent = 'Mencari nama tempat di sekitar titik…';
    const query = `[out:json][timeout:8];(node(around:250,${latitude},${longitude})[name];way(around:250,${latitude},${longitude})[name];relation(around:250,${latitude},${longitude})[name];);out center tags 20;`;
    try {
      const response = await fetch('https://overpass-api.de/api/interpreter', {
        method: 'POST',
        headers: {'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8'},
        body: 'data=' + encodeURIComponent(query)
      });
      if (!response.ok) throw new Error('Layanan tempat sekitar sedang sibuk');
      const data = await response.json();
      const candidates = (data.elements || []).map(item => {
        const tags = item.tags || {};
        const lat = item.lat ?? item.center?.lat;
        const lon = item.lon ?? item.center?.lon;
        if (lat == null || lon == null || !tags.name) return null;
        const distance = map.distance([latitude, longitude], [lat, lon]);
        const isBuildingLike = Boolean(tags.building || tags.amenity || tags.tourism ||
          tags.office || tags.leisure || tags.shop || tags.healthcare || tags.apartment);
        const type = tags.amenity || tags.tourism || tags.office || tags.leisure ||
          tags.shop || (tags.building ? 'gedung/bangunan' : tags.highway || 'tempat');
        return {name: tags.name, type, distance: Math.round(distance), lat, lon,
          priority: isBuildingLike ? 0 : 1};
      }).filter(Boolean)
        // Nama gedung/tempat diprioritaskan, tetapi tetap diberi jarak dan disebut kandidat.
        .sort((a,b) => a.priority - b.priority || a.distance - b.distance)
        .filter((item, index, arr) => arr.findIndex(other => other.name === item.name) === index)
        .slice(0, 5);
      output.replaceChildren();
      if (!candidates.length) {
        output.textContent = 'Belum ada nama tempat terpetakan di sekitar titik. Ini tidak membuktikan perangkat berada di tempat tertentu.';
        return;
      }
      const note = document.createElement('p');
      note.textContent = 'Kandidat tempat terdekat—bukan konfirmasi gedung tempat perangkat berada:';
      output.append(note);
      const list = document.createElement('ul');
      candidates.forEach(item => {
        const li = document.createElement('li');
        const link = document.createElement('a');
        link.href = `https://www.openstreetmap.org/?mlat=${item.lat}&mlon=${item.lon}#map=19/${item.lat}/${item.lon}`;
        link.target = '_blank'; link.rel = 'noopener noreferrer';
        link.textContent = `${item.name} — kandidat sekitar ${item.distance} m (${item.type})`;
        li.append(link); list.append(li);
      });
      output.append(list);
    } catch (e) {
      output.textContent = 'Nama tempat sekitar belum bisa dimuat. Coba lagi nanti; layanan publik bisa membatasi permintaan.';
    }
  }

  async function postJSON(url, body) {
    const response = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf}, body:JSON.stringify(body)});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Request gagal');
    return data;
  }
  function showPosition(pos) {
    const {latitude, longitude, accuracy} = pos.coords;
    $('gps-status').textContent = 'Aktif';
    $('gps-detail').textContent = 'Pembaruan diterima';
    $('coordinate').textContent = `${latitude.toFixed(5)}, ${longitude.toFixed(5)}`;
    const accuracyMeters = Math.round(accuracy);
    const quality = accuracyMeters <= 25 ? 'baik' : accuracyMeters <= 100 ? 'sedang' : 'rendah';
    $('accuracy').textContent = `Akurasi sekitar ±${accuracyMeters} meter (${quality}). Ini estimasi dari Windows/browser, bukan jaminan alamat tepat.`;
    lookupAddress(latitude, longitude);
    lookupNearbyPlaces(latitude, longitude);
    $('location-message').textContent = 'Lokasi diterima. Titik disimpan di database lokal.';
    const ll = [latitude, longitude];
    if (!marker) marker = L.marker(ll).addTo(map).bindPopup('Lokasi perangkat ini');
    marker.setLatLng(ll);
    if (!accuracyCircle) accuracyCircle = L.circle(ll, {radius:accuracy, color:'#20c997', fillOpacity:.08}).addTo(map);
    accuracyCircle.setLatLng(ll); accuracyCircle.setRadius(accuracy);
    map.setView(ll, 16);
    postJSON('/api/location', {latitude, longitude, accuracy, note:'Browser geolocation'}).then(refreshHistory).catch(err => $('location-message').textContent = err.message);
  }
  if ($('locate-btn')) $('locate-btn').addEventListener('click', () => {
    if (!('geolocation' in navigator)) { $('location-message').textContent = 'Browser tidak mendukung Geolocation API.'; return; }
    if (watchId !== null) return;
    $('location-message').textContent = 'Meminta izin lokasi dari browser…';
    watchId = navigator.geolocation.watchPosition(showPosition, err => {
      $('gps-status').textContent = 'Tidak tersedia';
      $('location-message').textContent = `${err.message} Pastikan izin lokasi diberikan dan halaman dibuka di localhost/HTTPS.`;
    }, {enableHighAccuracy:true, maximumAge:0, timeout:30000});
  });
  if ($('stop-gps-btn')) $('stop-gps-btn').addEventListener('click', () => {
    if (watchId !== null) navigator.geolocation.clearWatch(watchId);
    watchId = null; $('gps-status').textContent = 'Dihentikan';
    $('gps-detail').textContent = 'Tidak ada pembaruan baru';
    $('location-message').textContent = 'Pembaruan lokasi dihentikan.';
  });
  async function refreshHistory() {
    const res = await fetch('/api/locations');
    if (!res.ok) return;
    const rows = await res.json(), body = $('history-body');
    body.replaceChildren();
    if (!rows.length) { const tr=document.createElement('tr'), td=document.createElement('td'); td.colSpan=5; td.textContent='Belum ada titik lokasi.'; td.className='muted'; tr.append(td); body.append(tr); return; }
    rows.forEach(r => {
      const tr=document.createElement('tr');
      [r.created_at, Number(r.latitude).toFixed(6), Number(r.longitude).toFixed(6), `${Math.round(r.accuracy||0)} m`, r.note||''].forEach(v => { const td=document.createElement('td'); td.textContent=v; tr.append(td); });
      body.append(tr);
    });
  }
  $('refresh-history').addEventListener('click', () => selectedHistoryShareId ? refreshDeviceHistory(selectedHistoryShareId) : refreshHistory());

  if ($('camera-start-btn')) $('camera-start-btn').addEventListener('click', async () => {
    if (!navigator.mediaDevices?.getUserMedia) { $('camera-message').textContent='Camera API tidak tersedia. Gunakan browser modern di localhost/HTTPS.'; return; }
    try {
      stream = await navigator.mediaDevices.getUserMedia({video:{facingMode:'user'}, audio:false});
      $('camera-preview').srcObject = stream;
      $('camera-status').textContent='Aktif';
      $('camera-message').textContent='Preview aktif di browser ini. Tidak ada gambar dikirim sebelum kamu menekan Simpan snapshot.';
      $('snapshot-btn').disabled=false; $('camera-stop-btn').disabled=false; $('camera-start-btn').disabled=true;
    } catch (e) { $('camera-message').textContent=`Kamera tidak dapat diaktifkan: ${e.message}`; }
  });
  if ($('camera-stop-btn')) $('camera-stop-btn').addEventListener('click', stopCamera);
  function stopCamera() {
    if (stream) stream.getTracks().forEach(track => track.stop());
    stream=null; $('camera-preview').srcObject=null; $('camera-status').textContent='Nonaktif';
    $('snapshot-btn').disabled=true; $('camera-stop-btn').disabled=true; $('camera-start-btn').disabled=false;
    $('camera-message').textContent='Kamera dimatikan.';
  }
  if ($('snapshot-btn')) $('snapshot-btn').addEventListener('click', async () => {
    const video=$('camera-preview');
    if (!stream || !video.videoWidth) return;
    const canvas=document.createElement('canvas'); canvas.width=video.videoWidth; canvas.height=video.videoHeight;
    canvas.getContext('2d').drawImage(video,0,0);
    const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',0.88));
    const form=new FormData(); form.append('image', blob, 'snapshot.jpg');
    const response=await fetch('/api/camera-snapshot',{method:'POST',headers:{'X-CSRF-Token':csrf},body:form});
    const data=await response.json().catch(()=>({}));
    $('camera-message').textContent=response.ok ? `Snapshot disimpan lokal: ${data.filename}` : (data.error||'Gagal menyimpan snapshot.');
  });
  window.addEventListener('beforeunload', () => { if (watchId !== null) navigator.geolocation.clearWatch(watchId); if (stream) stream.getTracks().forEach(t=>t.stop()); });

  async function refreshDeviceMonitor() {
    const host = $('device-monitor');
    if (!host) return;
    try {
      const response = await fetch('/api/admin/devices', {
        cache: 'no-store', credentials: 'same-origin',
        headers: {'Accept': 'application/json'}
      });
      if (!response.ok) {
        host.textContent = `Gagal memuat perangkat (HTTP ${response.status}). Pastikan sesi admin masih login.`;
        return;
      }
      const devices = await response.json();
      const onlineDevices = Array.isArray(devices) ? devices.filter(d => d.online) : [];
      if (onlineDevices.length === 0) {
        host.textContent = 'Tidak ada perangkat yang sedang online.';
        return;
      }
      host.replaceChildren();
      onlineDevices.forEach(d => {
        const card = document.createElement('div');
        card.className = 'activity';
        const dot = document.createElement('span');
        dot.className = 'activity-dot';
        dot.style.background = d.online ? '#20c997' : '#8290a5';
        const body = document.createElement('div');
        const title = document.createElement('strong');
        title.textContent = `${d.online ? 'ONLINE' : 'OFFLINE'} · ${d.device_label || 'Perangkat'} · ${d.ip_address || 'IP tidak tersedia'} · ${String(d.share_id || '').slice(0,8)}`;
        title.style.cursor = "pointer";
        title.title = "Klik untuk memuat riwayat lokasi perangkat ini";
        title.addEventListener("click", () => {
          selectedHistoryShareId = d.share_id;
          refreshDeviceHistory(d.share_id);
        });
        const detail = document.createElement('small');
        const loc = d.location_consent && d.latitude != null && d.longitude != null
          ? `Lokasi: ${Number(d.latitude).toFixed(5)}, ${Number(d.longitude).toFixed(5)} (akurasi ±${Math.round(d.accuracy || 0)} m)`
          : 'Lokasi: menunggu persetujuan pengguna';
        const cam = d.camera_consent
          ? 'Kamera: izin diberikan; video belum disiarkan ke admin'
          : 'Kamera: menunggu persetujuan pengguna';
        detail.textContent = `${loc} · ${cam} · ${d.browser_guess || 'Browser?'} / ${d.os_guess || 'OS?'} · Terakhir terlihat ${d.seconds_since_seen ?? '?'} dtk lalu · ${d.user_agent || 'Browser tidak diketahui'}`;
        const activityButton = document.createElement('button');
        activityButton.type = 'button';
        activityButton.className = 'secondary';
        activityButton.textContent = 'Lihat aktivitas';
        activityButton.style.marginTop = '8px';
        const activityBox = document.createElement('div');
        activityBox.className = 'device-activity-details';
        activityBox.hidden = true;
        activityButton.addEventListener('click', async () => {
          if (!activityBox.hidden) {
            activityBox.hidden = true;
            activityButton.textContent = 'Lihat aktivitas';
            return;
          }
          activityBox.hidden = false;
          activityButton.textContent = 'Sembunyikan aktivitas';
          activityBox.textContent = 'Memuat aktivitas perangkat…';
          try {
            const r = await fetch(`/api/admin/device-activity?share_id=${encodeURIComponent(d.share_id)}`, {
              cache: 'no-store', credentials: 'same-origin'
            });
            const items = await r.json();
            activityBox.replaceChildren();
            if (!r.ok || !Array.isArray(items) || items.length === 0) {
              activityBox.textContent = 'Belum ada aktivitas untuk perangkat ini.';
            } else {
              items.forEach(item => {
                const line = document.createElement('p');
                line.className = 'muted';
                const strong = document.createElement('strong');
                strong.textContent = (item.event_type || 'activity').replaceAll('_', ' ');
                const small = document.createElement('small');
                small.textContent = `${item.created_at || ''} · ${item.detail || ''}`;
                line.append(strong, document.createElement('br'), small);
                activityBox.append(line);
              });
            }
          } catch (_) {
            activityBox.textContent = 'Aktivitas tidak dapat dimuat.';
          }
        });
        body.append(title, detail, activityButton, activityBox);
        // Ringkasan admin mengikuti lokasi terbaru dari perangkat yang online dan setuju.
        if (d.online && d.location_consent && d.stopped_at == null && d.latitude != null && d.longitude != null) {
          $("gps-status").textContent = "Lokasi pengguna diterima";
          $("gps-detail").textContent = `Diterima dari perangkat ${String(d.share_id || "").slice(0,8)}`;
          $("coordinate").textContent = `${Number(d.latitude).toFixed(5)}, ${Number(d.longitude).toFixed(5)}`;
          $("accuracy").textContent = `Akurasi perangkat pengguna ±${Math.round(d.accuracy || 0)} m`;
          $("camera-status").textContent = d.camera_consent ? "Izin kamera diberikan" : "Belum diizinkan";
        }
        card.append(dot, body);
        host.append(card);
        if (d.online && d.location_consent && d.stopped_at == null && d.latitude != null && typeof map !== 'undefined') {
          const ll = [Number(d.latitude), Number(d.longitude)];
          if (!window.cyberTraceVisitorMarkers) window.cyberTraceVisitorMarkers = {};
          let entry = window.cyberTraceVisitorMarkers[d.share_id];
          if (!entry) {
            entry = {
              marker: L.marker(ll).addTo(map),
              circle: L.circle(ll, {radius:Number(d.accuracy || 0), color:'#a78bfa', fillOpacity:.07}).addTo(map)
            };
            window.cyberTraceVisitorMarkers[d.share_id] = entry;
          }
          entry.marker.setLatLng(ll).bindPopup(`Pengguna ${d.ip_address || ''} · ${String(d.share_id || '').slice(0,8)}`);
          entry.circle.setLatLng(ll).setRadius(Number(d.accuracy || 0));
          if (!window.cyberTraceInitialLocationCentered) {
            map.setView(ll, 16);
            window.cyberTraceInitialLocationCentered = true;
            lookupAddress(Number(d.latitude), Number(d.longitude));
            lookupNearbyPlaces(Number(d.latitude), Number(d.longitude));
          }
        }
      });
    } catch (error) {
      host.textContent = `Gagal memuat daftar perangkat: ${error.message || error}. Periksa Console Flask/browser.`;
    }
  }
  refreshDeviceMonitor();
  window.setInterval(refreshDeviceMonitor, 3000);

  async function refreshDeviceHistory(shareId) {
    const body = $("history-body");
    if (!body || !shareId) return;
    try {
      const r = await fetch(`/api/admin/location-history?share_id=${encodeURIComponent(shareId)}`, {cache:"no-store",credentials:"same-origin"});
      if (!r.ok) throw new Error("Riwayat gagal dimuat");
      const rows = await r.json();
      body.replaceChildren();
      if (!rows.length) {
        const tr=document.createElement("tr"), td=document.createElement("td");
        td.colSpan=5; td.className="muted"; td.textContent="Belum ada riwayat lokasi yang dibagikan perangkat ini.";
        tr.append(td); body.append(tr); return;
      }
      rows.forEach(r => {
        const tr=document.createElement("tr");
        [r.created_at,Number(r.latitude).toFixed(6),Number(r.longitude).toFixed(6),`${Math.round(r.accuracy||0)} m`,r.note||""].forEach(v=>{
          const td=document.createElement("td"); td.textContent=v; tr.append(td);
        });
        body.append(tr);
      });
    } catch(e) { console.warn(e); }
  }

  async function pollLiveEvents() {
    const host=$("live-events");
    if (!host) return;
    try {
      const r=await fetch("/api/admin/events",{cache:"no-store",credentials:"same-origin"});
      if (!r.ok) return;
      const rows=await r.json();
      host.replaceChildren();
      if (!rows.length) {
        const p=document.createElement("p"); p.className="muted"; p.textContent="Belum ada aktivitas."; host.append(p); return;
      }
      for (const ev of rows) {
        const item=document.createElement("div"); item.className="activity";
        const dot=document.createElement("span"); dot.className="activity-dot";
        const content=document.createElement("div");
        const title=document.createElement("strong"); title.textContent=String(ev.event_type||"event").replaceAll("_"," ");
        const detail=document.createElement("small"); detail.textContent=`${ev.created_at} · ${ev.detail||"—"}`;
        content.append(title,detail); item.append(dot,content); host.append(item);
      }
    } catch(e) { /* next poll retries */ }
  }
  pollLiveEvents();
  window.setInterval(pollLiveEvents, 2500);


  async function refreshSharedSnapshots() {
    const host = $("shared-snapshots");
    if (!host) return;
    try {
      const r = await fetch("/api/admin/snapshots", {cache:"no-store", credentials:"same-origin"});
      if (!r.ok) return;
      const items = await r.json();
      host.replaceChildren();
      if (!items.length) {
        const p=document.createElement("p"); p.className="message";
        p.textContent="Belum ada foto JPG yang dikirim pengguna."; host.append(p); return;
      }
      items.forEach(item => {
        const card=document.createElement("div"); card.className="snapshot-card";
        const link=document.createElement("a"); link.href=item.url; link.target="_blank"; link.rel="noopener noreferrer";
        const img=document.createElement("img"); img.src=item.url; img.alt="Foto yang dibagikan pengguna"; img.loading="lazy";
        link.append(img);
        const meta=document.createElement("small"); meta.textContent=`${item.filename} · ${item.modified}`;
        const del=document.createElement("button");
        del.type="button"; del.className="snapshot-delete"; del.textContent="Hapus foto";
        del.setAttribute("aria-label",`Hapus ${item.filename}`);
        del.addEventListener("click", async () => {
          if (!confirm(`Hapus foto ini?\\n${item.filename}`)) return;
          del.disabled=true;
          try {
            const response=await fetch(`/api/admin/snapshots/${encodeURIComponent(item.filename)}`,{
              method:"DELETE", credentials:"same-origin", headers:{"X-CSRF-Token":csrf}
            });
            const result=await response.json().catch(()=>({}));
            if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
            await refreshSharedSnapshots();
          } catch(e) {
            alert(`Foto gagal dihapus: ${e.message || e}`);
            del.disabled=false;
          }
        });
        card.append(link,meta,del); host.append(card);
      });
    } catch (_) {}
  }
  refreshSharedSnapshots();
  window.setInterval(refreshSharedSnapshots, 3000);
})();
