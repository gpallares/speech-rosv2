import time
import wave
import logging

from os import path
from multiprocessing import Process, Pipe, Event, log_to_stderr, Queue
from multiprocessing.synchronize import Event as EventClass
from multiprocessing.connection import _ConnectionBase

from datetime import datetime
from typing import Optional, Generator
from tactigon_gear import TSkin, TSkinConfig, Hand, OneFingerGesture, TwoFingerGesture
from tactigon_gear.models import TBleSelector, GestureConfig


class Audio(Process):
    _TICK: float = 0.02
    _PIPE_TIMEOUT: float = 0.1
    _SAMPLE_RATE: float = 16000

    def __init__(self, stop: EventClass, pipe: _ConnectionBase, listen: EventClass, debug: bool = False):
        Process.__init__(self)
        self.logger = log_to_stderr()
        if debug:
            self.logger.setLevel(logging.DEBUG)

        self.stop = stop
        self.pipe = pipe
        self.listen = listen

    def vad_collector(self) -> Generator[bytes, bytes, None]:
        seconds = 5
        frame_length = 80
        num_samples = 2 * self._SAMPLE_RATE * seconds / frame_length

        frame = b''
        num_frames = 0
    
        while True:
            if num_frames >= num_samples:
                break
            
            if not self.pipe.poll():
                time.sleep(self._TICK)
                continue
            
            frame = self.pipe.recv_bytes()
            num_frames += 1

            yield frame

        return None

    def run(self):
        self.logger.debug("[Audio] Process started!")
        while not self.stop.is_set():
            if not self.listen.is_set():
                time.sleep(self._TICK)
                continue
            self.logger.debug("[Audio] Listening started!")
            i = 0
            with wave.open("test_audio.wav", "wb") as test_file:
                test_file.setsampwidth(2)
                test_file.setnchannels(1)
                test_file.setframerate(16000)

                for frame in self.vad_collector():
                    if frame is None:
                        break
                    test_file.writeframes(frame)
                    i += 1

            self.listen.clear()
            self.logger.debug("[Audio] Listening ended! Processes %i packets", i)

class TSkin_Audio(TSkin):
    def __init__(self, config: TSkinConfig, debug: bool = False):
        TSkin.__init__(self, config, debug)
        self._audio_rx, self._audio_tx = Pipe(duplex=False)
        self._audio_stop = Event()
        self._listen_event = Event()
        self.audio = Audio(self._audio_stop, self._audio_rx, self._listen_event, debug)

    def start(self):
        self.audio.start()
        TSkin.start(self)

    def join(self, timeout: Optional[float] = None):
        self.select_sensors()
        if self._listen_event.is_set():
            self.stop_listen()

        self._audio_stop.set()
        self.audio.join(timeout)
        self.audio.terminate()
        TSkin.join(self, timeout)

    def listen(self):
        logging.debug("[TSkin] Starting listen..")
        self._listen_event.set()
        self.select_audio()

        while self._listen_event.is_set():
            time.sleep(self.TICK)

        logging.debug("[TSkin] Stopped listen..")
        self.select_sensors()

    def stop_listen(self):
        logging.debug("[TSkin] Stopping listen..")
        self.select_sensors()
        self._listen_event.clear()
        self.clear_audio_pipe()

    def clear_audio_pipe(self):
        i = 0
        while self._audio_rx.poll():
            _ = self._audio_rx.recv_bytes()
            i += 1

        logging.debug("[Audio] Cleare %i packet from pipe", i)

def test():
    model_folder = path.join(path.abspath("."), "data", "models", "MODEL_01_R")

    gconfig = GestureConfig(
        path.join(model_folder, "model.pickle"), 
        path.join(model_folder, "encoder.pickle"),
        "test",
        datetime.now()
    )

    tskin_cfg = TSkinConfig(
        "C0:83:3D:34:25:38", #"E6:00:EC:EA:A8:A1",
        Hand.RIGHT,
        # gesture_config=gconfig
    )

    with TSkin_Audio(tskin_cfg, False) as tskin:
        print("connecting")
        while not tskin.connected:
            time.sleep(0.5)
        print("connected")
        
        while True:
            
            if not tskin.connected:
                print("Disconnected...")
                time.sleep(2)
                continue



            if tskin.selector == TBleSelector.AUDIO:
                print("Listening...")
                time.sleep(0.1)
                continue


            acc = tskin.acceleration
            gyro = tskin.gyro
            angle = tskin.angle
            batt = tskin.battery
            gest = tskin.gesture
            t = tskin.touch

            touch = ""
            if t:
                touch = t.one_finger if t.one_finger != OneFingerGesture.NONE else t.two_finger

            if touch == OneFingerGesture.SINGLE_TAP:
                tskin.listen()
                time.sleep


            if acc and gyro and angle:
                print(f"{round(angle.roll, 2)}\t{round(angle.pitch, 2)}\t{round(angle.yaw, 2)}\t{round(acc.x, 3)}\t{round(acc.y, 3)}\t{round(acc.z, 3)}\t{round(gyro.x, 3)}\t{round(gyro.y, 3)}\t{round(gyro.z, 3)}\t{batt}\t{gest.gesture if gest else ''}\t{touch}")                
                pass
            
            time.sleep(0.02)

def main():
    test()

if __name__ == "__main__":
    main()