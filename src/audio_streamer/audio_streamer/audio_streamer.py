import socket
import wave
import rclpy
from rclpy.node import Node

class AudioStreamer(Node):
    def __init__(self):
        super().__init__('audio_streamer')
        # Declare ROS2 parameters
        self.declare_parameter('wav_path', 'input.wav')
        self.declare_parameter('host', '0.0.0.0')
        self.declare_parameter('port', 5000)
        self.declare_parameter('chunk_size', 4096)

        self.wav_path = self.get_parameter('wav_path').get_parameter_value().string_value
        self.host = self.get_parameter('host').get_parameter_value().string_value
        self.port = self.get_parameter('port').get_parameter_value().integer_value
        self.chunk_size = self.get_parameter('chunk_size').get_parameter_value().integer_value

        self.get_logger().info(f"Streaming '{self.wav_path}' on {self.host}:{self.port} (chunk={self.chunk_size} bytes)")
        self.stream_wav()

    def stream_wav(self):
        # Open WAV file
        with wave.open(self.wav_path, 'rb') as wf:
            # Create TCP server
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.bind((self.host, self.port))
            srv.listen(1)
            self.get_logger().info('Waiting for client to connect...')
            conn, addr = srv.accept()
            self.get_logger().info(f'Client connected from {addr}')

            # Stream in chunks 
            data = wf.readframes(self.chunk_size)
            while data:
                conn.sendall(data)
                data = wf.readframes(self.chunk_size)

            self.get_logger().info('Streaming complete, closing connection.')
            conn.close()
            srv.close()

def main(args=None):
    rclpy.init(args=args)
    node = AudioStreamer()
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()