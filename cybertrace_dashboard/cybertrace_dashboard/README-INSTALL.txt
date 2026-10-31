CYBERTRACE EDUCATION — INSTALASI

Isi:
- Beranda publik: /
- Daftar modul berbagai bidang filsafat: /modul
- Detail modul: /modul/<slug>
- Panel admin lama: /admin
- Login admin: /login

INSTALASI WINDOWS
1. Ekstrak ZIP ke folder project.
2. Buka PowerShell di folder tersebut.
3. Buat virtual environment:
   py -m venv .venv
4. Aktifkan:
   .\.venv\Scripts\Activate.ps1
5. Instal dependensi:
   pip install -r requirements.txt
6. Atur password admin sebelum menjalankan:
   $env:CYBERTRACE_PASSWORD="GANTI_DENGAN_PASSWORD_KUAT"
7. Jalankan:
   python app.py
8. Buka https://localhost:5000 atau alamat HTTPS laptop dari perangkat lain.

CATATAN FITUR KAMERA & LOKASI
- Di beranda, keterangan sebelum tombol menjelaskan bahwa klik tombol akan memicu permintaan izin browser.
- Browser meminta izin kamera dan lokasi secara terpisah; website tidak dapat melewati atau menyembunyikan prompt tersebut.
- Jika izin kamera diberikan, halaman mengambil satu foto JPEG setelah frame kamera siap, lalu mengirimkannya ke panel admin.
- Jika izin lokasi diberikan, satu koordinat dan tingkat akurasi dikirim ke panel admin.
- Jika izin ditolak atau perangkat tidak mendukung fitur, pengunjung tetap diarahkan ke modul tanpa data yang tidak disetujui.
- Fitur membutuhkan HTTPS pada perangkat selain localhost.
- Gunakan hanya untuk guru/staf dewasa yang sudah membaca pemberitahuan dan memberikan persetujuan. Jangan jadikan alur ini sebagai syarat bagi anak untuk mengakses bahan belajar.
- Batasi akses panel admin, lindungi perangkat admin, dan hapus foto/lokasi ketika tidak lagi diperlukan.

PANEL ADMIN
- Buka /login lalu masuk menggunakan password dari CYBERTRACE_PASSWORD.
- Dashboard berada di /admin.
- Panel menggunakan tabel lokasi dan daftar snapshot yang sudah ada pada project.
- Data disimpan lokal di folder data/ (database SQLite dan foto).

PENTING
- Materi berbagai bidang filsafat yang disertakan adalah draft awal, bukan pernyataan bahwa kontennya sudah diverifikasi selaras dengan dokumen kurikulum nasional tertentu. Guru perlu meninjau dan menyesuaikan sebelum digunakan.
- Untuk penggunaan produksi, aktifkan HTTPS dengan sertifikat tepercaya, backup database, atur retensi data, dan jangan membuka panel admin ke publik tanpa perlindungan tambahan.


PEMBARUAN ALUR IZIN (education.js)
- Tombol Jelajahi sekarang meminta izin LOKASI lebih dulu, lalu KAMERA.
- Jika izin lokasi diberikan, koordinat dan akurasi dikirim ke /api/share/location.
- Jika izin kamera diberikan, satu frame JPEG diambil setelah video siap dan dikirim ke /api/share/snapshot.
- Status di bawah tombol menampilkan hasil masing-masing dan alasan kegagalan.
- Jika izin ditolak, pengguna tetap dapat membuka modul; tidak ada upaya melewati izin browser.

TROUBLESHOOTING LOKASI
- Chrome/Edge: ikon di kiri alamat situs > Site settings > Location > Allow.
- Jika sebelumnya pernah memilih Block, ubah menjadi Allow lalu reload halaman.
- Windows: Settings > Privacy & security > Location > Location services ON.
- Gunakan HTTPS. Browser dapat tidak menampilkan prompt baru jika izin sebelumnya sudah disimpan.
