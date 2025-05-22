import socket
import wave
import argparse

parser = argparse.ArgumentParser()
parser.add_argument('--host', default='127.0.0.1', help='Server IP address')
parser.add_argument('--port', type=int, default=5013, help='Server port')
parser.add_argument('--output', default='received.wav', help='Output WAV file')
args = parser.parse_args()

HOST = args.host
PORT = args.port
OUT_PATH = args.output
CHUNK = 4096

# Connect to ROS2 streamer
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect((HOST, PORT))

# We need to know WAV parameters (nchannels, sampwidth, framerate).
# Option 1: negotiate via header or prior agreement. Here we assume known parameters.
# For production, you could send a small JSON header first.

# For simplicity, mic on tactigon is mono, 16-bit PCM, 16kHz.
params = {'nchannels': 1, 'sampwidth': 2, 'framerate': 16000}

with wave.open(OUT_PATH, 'wb') as wf:
    wf.setnchannels(params['nchannels'])
    wf.setsampwidth(params['sampwidth'])
    wf.setframerate(params['framerate'])
    while True:
        data = s.recv(CHUNK)
        if not data:
            break
        wf.writeframes(data)

s.close()
print(f'Received audio saved to {OUT_PATH}')