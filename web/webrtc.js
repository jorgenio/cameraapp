/* Cliente WebRTC mínimo para go2rtc (señalización vía nuestro servidor). */
(function () {
  "use strict";

  function waitIce(pc, timeout) {
    return new Promise((resolve) => {
      if (pc.iceGatheringState === "complete") return resolve();
      const done = () => {
        pc.removeEventListener("icegatheringstatechange", check);
        clearTimeout(timer);
        resolve();
      };
      const check = () => {
        if (pc.iceGatheringState === "complete") done();
      };
      const timer = setTimeout(done, timeout);
      pc.addEventListener("icegatheringstatechange", check);
    });
  }

  class VSStream {
    constructor(video, camId, opts) {
      this.video = video;
      this.camId = camId;
      this.opts = opts || {};
      this.pc = null;
      this.stream = null;
      this.closed = false;
      this.retries = 0;
      this.onState = this.opts.onState || function () {};
      this._retryTimer = null;
    }

    async start() {
      if (this.closed) return;
      if (typeof RTCPeerConnection === "undefined") {
        this.onState("unsupported");
        return;
      }
      this._teardown();
      this.onState("connecting");

      const pc = new RTCPeerConnection({ iceServers: [] });
      this.pc = pc;
      const stream = new MediaStream();
      this.stream = stream;
      this.video.srcObject = stream;

      pc.ontrack = (ev) => {
        if (ev.streams && ev.streams[0]) {
          this.stream = ev.streams[0];
          this.video.srcObject = this.stream;
        } else if (!stream.getTracks().includes(ev.track)) {
          stream.addTrack(ev.track);
        }
      };

      pc.onconnectionstatechange = () => {
        const st = pc.connectionState;
        if (this.closed) return;
        if (st === "connected") {
          this.retries = 0;
          this.onState("playing");
        } else if (st === "failed") {
          this.onState("failed");
          this._scheduleRetry();
        } else if (st === "disconnected") {
          this.onState("reconnecting");
          this._scheduleRetry(1500);
        }
      };

      try {
        pc.addTransceiver("video", { direction: "recvonly" });
        pc.addTransceiver("audio", { direction: "recvonly" });
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        await waitIce(pc, 2500);
        if (this.closed) return;

        const res = await fetch("/api/webrtc?src=" + encodeURIComponent(this.camId), {
          method: "POST",
          headers: { "Content-Type": "application/sdp" },
          body: pc.localDescription.sdp,
        });
        if (!res.ok) {
          let msg = "HTTP " + res.status;
          try { const j = await res.json(); if (j.error) msg = j.error; } catch (e) {}
          throw new Error(msg);
        }
        const answer = await res.text();
        if (this.closed) return;
        await pc.setRemoteDescription({ type: "answer", sdp: answer });

        const waitForPlay = setTimeout(() => {
          if (pc.connectionState !== "connected" && !this.closed) {
            this.onState("failed");
            this._scheduleRetry();
          }
        }, 9000);
        pc.addEventListener("connectionstatechange", () => clearTimeout(waitForPlay), { once: true });

        this.video.play().catch(() => {});
      } catch (err) {
        if (this.closed) return;
        this.onState("failed", err && err.message ? err.message : String(err));
        this._scheduleRetry();
      }
    }

    _scheduleRetry(delay) {
      if (this.closed || this._retryTimer) return;
      this.retries += 1;
      if (this.retries > 4) {
        this.onState("unavailable");
        return;
      }
      const wait = delay || Math.min(2000 * this.retries, 10000);
      this._retryTimer = setTimeout(() => {
        this._retryTimer = null;
        this.start();
      }, wait);
    }

    _teardown() {
      if (this._retryTimer) {
        clearTimeout(this._retryTimer);
        this._retryTimer = null;
      }
      if (this.pc) {
        try { this.pc.close(); } catch (e) {}
        this.pc = null;
      }
    }

    stop() {
      this.closed = true;
      this._teardown();
      this.video.srcObject = null;
    }

    setAudio(on) {
      this.video.muted = !on;
      if (on) this.video.play().catch(() => {});
    }
  }

  window.VSStream = VSStream;
})();
