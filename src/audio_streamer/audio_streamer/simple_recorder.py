import time
import wave
import logging
from multiprocessing import Process, Pipe, Event
from tactigon_gear import TSkin, TSkinConfig, Hand
from tactigon_gear.models import TBleSelector

# --- Audio Recorder Process ---
class AudioRecorder(Process):
    """Collects raw PCM frames from a Pipe and writes them to a WAV file."""
    FRAME_TIMEOUT = 0.02
    SAMPLE_RATE = 16000
    CHANNELS = 1
    SAMPLE_WIDTH = 2  # bytes (16-bit)

    def __init__(self, stop_event: Event, data_pipe, output_path: str = "recorded.wav"):
        super().__init__()
        self.stop_event = stop_event
        self.data_pipe = data_pipe
        self.output_path = output_path
        self.logger = logging.getLogger("AudioRecorder")
        self.logger.setLevel(logging.INFO)

    def run(self):
        self.logger.info(f"Recording audio to '{self.output_path}'...")
        with wave.open(self.output_path, "wb") as wf:
            wf.setnchannels(self.CHANNELS)
            wf.setsampwidth(self.SAMPLE_WIDTH)
            wf.setframerate(self.SAMPLE_RATE)

            while not self.stop_event.is_set():
                if self.data_pipe.poll(self.FRAME_TIMEOUT):
                    frame = self.data_pipe.recv_bytes()
                    wf.writeframes(frame)
                else:
                    time.sleep(self.FRAME_TIMEOUT)

        self.logger.info("Stopped recording.")

# --- TSkin wrapper that forwards audio to recorder ---
class TSkinAudio(TSkin):
    def __init__(self, config: TSkinConfig, debug: bool = False):
        super().__init__(config, debug)
        self._audio_rx, self._audio_tx = Pipe(duplex=False)
        self._stop_evt = Event()
        self.recorder = AudioRecorder(self._stop_evt, self._audio_rx, output_path="test_audio.wav")

    def start(self):
        self.recorder.start()
        super().start()

    def stop(self):
        # Stop BLE and recorder
        self._stop_evt.set()
        self.recorder.join()
        super().join()

    def listen_audio(self, duration: float = None):
        """
        Start streaming BLE audio into the WAV recorder.
        If duration is given, stop after that many seconds.
        """
        end_time = time.time() + duration if duration else None
        self.select_audio()  # switch BLE to audio mode

        try:
            while True:
                if end_time and time.time() >= end_time:
                    break
                # Poll BLE selector for audio packets
                if self.selector == TBleSelector.AUDIO and self._connection:
                    data = self._connection.recv()  # raw PCM bytes
                    self._audio_tx.send_bytes(data)
                time.sleep(AudioRecorder.FRAME_TIMEOUT)
        finally:
            self.select_sensors()  # back to sensors mode

# --- Example usage ---
def main():
    logging.basicConfig(level=logging.INFO)
    cfg = TSkinConfig(
        "C0:83:3D:34:25:38",
        Hand.RIGHT
    )
    tskin = TSkinAudio(cfg, debug=False)
    tskin.start()
    print("Connected. Recording 5 seconds of audio...")
    tskin.listen_audio(duration=5.0)
    print("Done. File saved to 'test_audio.wav'.")
    tskin.stop()

if __name__ == "__main__":
    main()
