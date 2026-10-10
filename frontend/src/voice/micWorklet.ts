// 麦克风采集：在音频线程里把设备采样率降到 16kHz，每 20ms 发一帧 Int16 PCM 给主线程。
// 以字符串形式注册成 AudioWorklet 模块，避免额外的构建配置。
export const MIC_WORKLET = `
class MicProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.position = 0;
    this.frame = new Int16Array(320);
    this.filled = 0;
  }
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    // 线性插值降采样：position 是下一个输出样本在输入里的位置
    while (this.position < channel.length) {
      const index = Math.floor(this.position);
      const next = Math.min(index + 1, channel.length - 1);
      const fraction = this.position - index;
      const value = channel[index] * (1 - fraction) + channel[next] * fraction;
      const clipped = Math.max(-1, Math.min(1, value));
      this.frame[this.filled++] = clipped < 0 ? clipped * 0x8000 : clipped * 0x7fff;
      if (this.filled === this.frame.length) {
        this.port.postMessage(this.frame.buffer.slice(0));
        this.filled = 0;
      }
      this.position += this.ratio;
    }
    this.position -= channel.length;
    return true;
  }
}
registerProcessor("mic-processor", MicProcessor);
`;
