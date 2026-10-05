export class GameAudio {
  constructor() {
    this.volume = 0.25;
  }
  unlock() {
    if (!this.context) {
      const Audio = globalThis.AudioContext ?? globalThis.webkitAudioContext;
      if (Audio) this.context = new Audio();
    }
    this.context?.resume().catch(() => {});
  }
  play(placing) {
    if (!this.context || !this.volume) return;
    const ctx = this.context,
      t = ctx.currentTime,
      osc = ctx.createOscillator(),
      gain = ctx.createGain();
    osc.type = "triangle";
    osc.frequency.setValueAtTime(placing ? 260 : 160, t);
    osc.frequency.exponentialRampToValueAtTime(placing ? 110 : 60, t + 0.075);
    gain.gain.setValueAtTime(this.volume * 0.14, t);
    gain.gain.exponentialRampToValueAtTime(0.001, t + 0.09);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(t);
    osc.stop(t + 0.1);
    osc.onended = () => {
      osc.disconnect();
      gain.disconnect();
    };
  }
}
