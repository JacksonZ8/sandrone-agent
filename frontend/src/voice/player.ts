/** 她的声音：把服务器推来的 PCM16 片段排队无缝播放，打断时立即全部停下。 */
export class PcmPlayer {
  private context: AudioContext;
  private output: GainNode;
  private sources = new Set<AudioBufferSourceNode>();
  private nextTime = 0;
  private sampleRate = 32000;
  private accepting = false;
  private ended = false;
  readonly analyser: AnalyserNode;
  onDrained: () => void = () => {};

  constructor(context: AudioContext) {
    this.context = context;
    this.output = context.createGain();
    this.analyser = context.createAnalyser();
    this.analyser.fftSize = 512;
    this.output.connect(this.analyser);
    this.analyser.connect(context.destination);
  }

  /** 新一轮回复开始；此前被打断的残余音频不会再播放 */
  begin(sampleRate: number): void {
    this.sampleRate = sampleRate;
    this.accepting = true;
    this.ended = false;
  }

  /** 服务器表示这一轮音频已全部发出 */
  end(): void {
    this.ended = true;
    this.checkDrained();
  }

  push(data: ArrayBuffer): void {
    if (!this.accepting) return;
    const pcm = new Int16Array(data);
    if (pcm.length === 0) return;
    const buffer = this.context.createBuffer(1, pcm.length, this.sampleRate);
    const channel = buffer.getChannelData(0);
    for (let index = 0; index < pcm.length; index += 1) channel[index] = pcm[index] / 32768;
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.output);
    // 留 40ms 余量，避免网络抖动造成的断续
    const startAt = Math.max(this.nextTime, this.context.currentTime + 0.04);
    source.start(startAt);
    this.nextTime = startAt + buffer.duration;
    this.sources.add(source);
    source.onended = () => {
      this.sources.delete(source);
      this.checkDrained();
    };
  }

  stop(): void {
    this.accepting = false;
    this.ended = false;
    for (const source of this.sources) {
      source.onended = null;
      try {
        source.stop();
      } catch {
        // 已经停止
      }
    }
    this.sources.clear();
    this.nextTime = 0;
  }

  get playing(): boolean {
    return this.sources.size > 0;
  }

  private checkDrained(): void {
    if (this.ended && this.sources.size === 0) {
      this.ended = false;
      this.onDrained();
    }
  }
}
