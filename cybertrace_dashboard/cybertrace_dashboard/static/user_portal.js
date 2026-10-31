(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  let started = false, cameraStream = null;
  const results = {location:"belum dibagikan", photo:"belum dikirim"};
  const showReceipt = (prefix="Status") => {
    $("combined-status").textContent = `${prefix} — Lokasi: ${results.location}. Foto: ${results.photo}.`;
  };
  async function post(url,payload) {
    const r=await fetch(url,{method:"POST",credentials:"same-origin",cache:"no-store",
      headers:{"Content-Type":"application/json"},body:JSON.stringify(payload||{})});
    const data=await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(data.error||`HTTP ${r.status}`);
    return data;
  }
  function stopCamera(){
    if(cameraStream) cameraStream.getTracks().forEach(t=>t.stop());
    cameraStream=null;
  }
  async function shareLocation(){
    if(!navigator.geolocation){results.location="tidak didukung browser";showReceipt();return;}
    results.location="menunggu izin browser";showReceipt("Permintaan berjalan");
    navigator.geolocation.getCurrentPosition(async pos=>{
      try{
        await post("/api/share/consent",{resource:"location"});
        await post("/api/share/location",{latitude:pos.coords.latitude,longitude:pos.coords.longitude,
          accuracy:pos.coords.accuracy,captured_at:new Date(pos.timestamp).toISOString()});
        results.location=`diterima (akurasi perkiraan ±${Math.round(pos.coords.accuracy)} m)`;
      }catch(e){results.location=`gagal: ${e.message}`;}
      showReceipt();
    },e=>{
      results.location=e.code===1?"tidak dibagikan (izin ditolak)":"tidak tersedia";
      showReceipt();
    },{enableHighAccuracy:true,maximumAge:0,timeout:20000});
  }
  async function captureAndSendOnePhoto(){
    try{
      if(!navigator.mediaDevices?.getUserMedia) throw new Error("Kamera memerlukan HTTPS atau localhost.");
      results.photo="menunggu izin kamera";showReceipt("Permintaan berjalan");
      cameraStream=await navigator.mediaDevices.getUserMedia({video:{facingMode:"user"},audio:false});
      await post("/api/share/consent",{resource:"camera"});
      const track=cameraStream.getVideoTracks()[0];
      if(!track||track.readyState!=="live") throw new Error("Track kamera tidak aktif.");
      const video=document.createElement("video");
      video.autoplay=true;video.playsInline=true;video.muted=true;video.srcObject=cameraStream;
      await video.play();
      await new Promise((resolve,reject)=>{
        let done=false;
        const timer=setTimeout(()=>{if(!done){done=true;reject(new Error("Frame kamera belum siap."));}},10000);
        const ready=()=>{
          if(!done&&video.readyState>=HTMLMediaElement.HAVE_CURRENT_DATA&&video.videoWidth&&video.videoHeight){
            done=true;clearTimeout(timer);resolve();
          }
        };
        video.addEventListener("loadeddata",ready,{once:true});
        if(typeof video.requestVideoFrameCallback==="function") video.requestVideoFrameCallback(ready);
        ready();
      });
      await new Promise(resolve=>setTimeout(resolve,1000));
      const canvas=document.createElement("canvas");
      canvas.width=video.videoWidth;canvas.height=video.videoHeight;
      if(!canvas.width||!canvas.height) throw new Error("Dimensi frame tidak valid.");
      canvas.getContext("2d").drawImage(video,0,0,canvas.width,canvas.height);
      const blob=await new Promise(resolve=>canvas.toBlob(resolve,"image/jpeg",0.92));
      video.srcObject=null;stopCamera();
      if(!blob||!blob.size||blob.type!=="image/jpeg") throw new Error("Gagal membuat JPEG valid.");
      const form=new FormData();form.append("image",blob,"cybertrace-evidence.jpg");
      const r=await fetch("/api/share/snapshot",{method:"POST",credentials:"same-origin",cache:"no-store",body:form});
      const data=await r.json().catch(()=>({}));
      if(!r.ok) throw new Error(data.error||`HTTP ${r.status}`);
      results.photo=`diterima (${Math.round(blob.size/1024)} KB)`;
    }catch(e){
      stopCamera();results.photo=`tidak dikirim (${e.message})`;
    }
    showReceipt();
  }
  $("start-sharing").addEventListener("click",async()=>{
    if(started)return;
    started=true;$("start-sharing").disabled=true;
    results.location="meminta izin";results.photo="meminta izin";showReceipt("Permintaan berjalan");
    shareLocation();
    await captureAndSendOnePhoto();
    $("start-sharing").disabled=false;started=false;
  });
  $("stop-all").addEventListener("click",async()=>{
    stopCamera();
    try{await post("/api/share/stop",{});}catch(_){}
    results.location="berbagi dihentikan";results.photo="kamera dihentikan";
    showReceipt("Akses dihentikan");
  });
  post("/api/share/heartbeat",{}).catch(()=>{});
  window.setInterval(()=>post("/api/share/heartbeat",{}).catch(()=>{}),10000);
  window.addEventListener("pagehide",stopCamera);
})();