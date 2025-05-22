import time
import socket
import rclpy
from rclpy.node import Node
from tactigon_gear import TSkinConfig, Hand, OneFingerGesture
from tactigon_gear.models import TBleSelector
from audio_streamer.recorder_tactigon import TSkin_Audio

class AudioStreamer(Node):
    def __init__(self):
        super().__init__('audio_streamer')
        self.declare_parameter('host', '0.0.0.0')
        self.declare_parameter('port', 5000)
        self.declare_parameter('ble_mac', 'C0:83:3D:34:25:38')
        self.declare_parameter('hand', 'RIGHT')

        self.host = self.get_parameter('host').get_parameter_value().string_value
        self.port = self.get_parameter('port').get_parameter_value().integer_value
        self.ble_mac = self.get_parameter('ble_mac').get_parameter_value().string_value
        self.hand = self.get_parameter('hand').get_parameter_value().string_value

        self.get_logger().info(f"Starting BLE audio stream from {self.ble_mac} on {self.host}:{self.port}")
        self.stream_audio()

    def stream_audio(self):
        hand_enum = Hand.RIGHT if self.hand.upper() == 'RIGHT' else Hand.LEFT
        tskin_cfg = TSkinConfig(self.ble_mac, hand_enum)
        tskin = TSkin_Audio(tskin_cfg, debug=False)
        tskin.start()
        self.get_logger().info('Connecting to Tactigon...')
        while not tskin.connected:
            time.sleep(0.5)
        self.get_logger().info('Connected to Tactigon.')

        # Setup TCP server
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind((self.host, self.port))
        srv.listen(1)
        self.get_logger().info('Waiting for client to connect...')
        conn, addr = srv.accept()
        self.get_logger().info(f'Client connected from {addr}')

        try:
            while True:
                # Wait for single tap gesture
                self.get_logger().info('Waiting for single tap gesture to start audio streaming...')
                # Wait until not in AUDIO mode
                while tskin.selector == TBleSelector.AUDIO:
                    self.get_logger().info('Device is already in AUDIO mode, waiting to return to sensor mode...')
                    time.sleep(0.1)
                # Wait for gesture
                while True:
                    t = tskin.touch
                    touch = t.one_finger if t and t.one_finger != OneFingerGesture.NONE else (t.two_finger if t else None)
                    if touch == OneFingerGesture.SINGLE_TAP:
                        self.get_logger().info('Single tap detected! Starting audio streaming.')
                        break
                    time.sleep(0.05)

                # Double-check not in AUDIO mode before starting
                if tskin.selector == TBleSelector.AUDIO:
                    self.get_logger().warn('Tried to start audio but device is already in AUDIO mode. Skipping.')
                    continue

                # Start listening for audio and stream to socket
                try:
                    tskin._listen_event.set()
                    tskin.select_audio()
                except Exception as e:
                    self.get_logger().error(f'Error starting audio mode: {e}')
                    tskin._listen_event.clear()
                    continue
                try:
                    seconds = 5
                    frame_length = 80
                    sample_rate = 16000
                    num_samples = int(2 * sample_rate * seconds / frame_length)
                    num_frames = 0
                    start_time = time.time()
                    while tskin._listen_event.is_set() and num_frames < num_samples:
                        if not tskin._audio_rx.poll(0.1):
                            time.sleep(0.02)
                            continue
                        frame = tskin._audio_rx.recv_bytes()
                        if not frame:
                            break
                        conn.sendall(frame)
                        num_frames += 1
                        # Optional: break after 5 seconds as a failsafe
                        if time.time() - start_time > 5.0:
                            self.get_logger().info('5 seconds elapsed, stopping audio streaming.')
                            break
                except Exception as e:
                    self.get_logger().error(f'Error during streaming: {e}')
                finally:
                    tskin._listen_event.clear()
                    tskin.select_sensors()
                    self.get_logger().info('Audio streaming stopped. Waiting for next gesture...')
        except Exception as e:
            self.get_logger().error(f'Error in main loop: {e}')
        finally:
            tskin.join()
            conn.close()
            srv.close()
            self.get_logger().info('Streaming complete, connection closed.')

def main(args=None):
    rclpy.init(args=args)
    node = AudioStreamer()
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()