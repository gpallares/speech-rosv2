#!/usr/bin/env python3

import time
# import wave 
import logging
import socket
import queue 
import threading 
import rclpy
from rclpy.node import Node

from os import path
from multiprocessing import Process, Pipe, Event, log_to_stderr, Queue as MpQueue
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

    def __init__(self, stop: EventClass, audio_input_pipe: _ConnectionBase,
                 audio_output_queue: MpQueue, listen: EventClass, debug: bool = False):
        Process.__init__(self)
        self.logger = log_to_stderr() 
        if debug:
            self.logger.setLevel(logging.DEBUG)
        else:
            self.logger.setLevel(logging.INFO)

        self.stop_event = stop
        self.audio_input_pipe = audio_input_pipe
        self.audio_output_queue = audio_output_queue
        self.listen_event = listen

    def vad_collector(self) -> Generator[bytes, None, None]:
        self.logger.debug("[AudioVadCollector] Starting to collect frames.")
        while not self.stop_event.is_set() and self.listen_event.is_set():
            if self.audio_input_pipe.poll(self._PIPE_TIMEOUT):
                try:
                    frame = self.audio_input_pipe.recv_bytes()
                    yield frame
                except EOFError:
                    self.logger.warning("[AudioVadCollector] Audio input pipe closed (EOFError).")
                    break
                except Exception as e:
                    self.logger.error(f"[AudioVadCollector] Error receiving from pipe: {e}")
                    break
            else:
                pass # Poll timeout, loop continues to check stop/listen events
        self.logger.debug("[AudioVadCollector] Stopped collecting frames.")
        return None

    def run(self):
        self.logger.info("[AudioProcess] Process started!")
        while not self.stop_event.is_set():
            if not self.listen_event.is_set():
                time.sleep(self._TICK)
                continue

            self.logger.info("[AudioProcess] Listening started, processing frames.")
            frames_processed = 0
            for frame in self.vad_collector():
                if frame is None:
                    self.logger.warning("[AudioProcess] vad_collector yielded None unexpectedly.")
                    break
                try:
                    self.audio_output_queue.put(frame, timeout=1.0)
                    frames_processed += 1
                except queue.Full:
                    self.logger.warning("[AudioProcess] Output queue is full. Frame dropped.")
                except Exception as e:
                    self.logger.error(f"[AudioProcess] Error putting frame to queue: {e}")
                    self.listen_event.clear()
                    break
            
            log_msg_processed = f"[AudioProcess] Processed {frames_processed} packets."
            if self.listen_event.is_set():
                self.logger.info(f"[AudioProcess] Listening loop ended. {log_msg_processed}")
                self.listen_event.clear()
            else:
                 self.logger.info(f"[AudioProcess] Listening externally stopped. {log_msg_processed}")

        self.logger.info("[AudioProcess] Process stopped!")

class TSkin_Audio(TSkin):
    def __init__(self, config: TSkinConfig, debug: bool = False):
        
        TSkin.__init__(self, config, debug)
        self._audio_rx, self._audio_tx = Pipe(duplex=False)
        
        self._audio_stop_event = Event()
        self._listen_event = Event()
        
        self.audio_data_queue = MpQueue(maxsize=100)

        self.audio_process = Audio(
            self._audio_stop_event,
            self._audio_rx, # Audio process reads from this pipe
            self.audio_data_queue,    # Audio process writes to this queue
            self._listen_event,
            debug
        )
        self._is_audio_started = False
        
    def start(self):
        if not self._is_audio_started and self.audio_process is not None:
            self.audio_process.start()
            self._is_audio_started = True
            logging.info("[TSkin_Audio] Audio process started.")
        
        TSkin.start(self)
        logging.info("[TSkin_Audio] TSkin (base) start called.")

    def join(self, timeout: Optional[float] = None):
        logging.info("[TSkin_Audio] Joining...")
        if self._listen_event.is_set():
            self.stop_listen()

        self._audio_stop_event.set()

        if self.audio_process and self.audio_process.is_alive():
            logging.debug("[TSkin_Audio] Joining Audio process...")
            self.audio_process.join(timeout=timeout if timeout else 5.0)
            if self.audio_process.is_alive():
                logging.warning("[TSkin_Audio] Audio process did not join in time, terminating.")
                self.audio_process.terminate()
            else:
                logging.info("[TSkin_Audio] Audio process joined successfully.")
        
        if self._audio_tx: self._audio_tx.close() 
        if self._audio_rx: self._audio_rx.close() 
        if self.audio_data_queue: 
            self.clear_audio_data_queue()
            self.audio_data_queue.close()
            self.audio_data_queue.join_thread() # Wait for queue's internal thread to finish

        TSkin.join(self, timeout)
        logging.info("[TSkin_Audio] Joined successfully.")

    def listen(self):
        if not self.connected:
            logging.warning("[TSkin_Audio] Cannot start listening: TSkin not connected.")
            return
        if not self.audio_process or not self.audio_process.is_alive():
            logging.warning("[TSkin_Audio] Cannot start listening: Audio process not running.")
            # Try to start it if it's not alive but should be?
            if not self._is_audio_started and self.audio_process is not None:
                logging.info("[TSkin_Audio] Audio process was not started, attempting to start now.")
                self.audio_process.start()
                self._is_audio_started = True
                time.sleep(0.5) # Give it a moment to start
                if not self.audio_process.is_alive():
                    logging.error("[TSkin_Audio] Failed to start audio process on demand.")
                    return
            elif not self.audio_process: 
                 logging.error("[TSkin_Audio] Audio process object does not exist.")
                 return

        logging.info("[TSkin_Audio] Starting listen...")
        self.clear_audio_data_queue()
        self._listen_event.set()
        self.select_audio() 
                            

    def stop_listen(self):
        logging.info("[TSkin_Audio] Stopping listen...")
        self.select_sensors()
        self._listen_event.clear()
        time.sleep(0.2) 
        self.clear_audio_data_queue()
        logging.debug("[TSkin_Audio] Cleared audio data queue after stopping listen.")

    def clear_audio_data_queue(self):
        count = 0
        while not self.audio_data_queue.empty():
            try:
                self.audio_data_queue.get_nowait()
                count += 1
            except queue.Empty:
                break
        if count > 0:
            logging.debug(f"[TSkin_Audio] Cleared {count} packets from audio data queue.")

# ROS 2 Node


class TactigonAudioStreamerNode(Node):
    def __init__(self):
        super().__init__('tactigon_audio_streamer')

        
        self.tskin: Optional[TSkin_Audio] = None
        self.server_socket: Optional[socket.socket] = None
        self.client_connection: Optional[socket.socket] = None
        self.streaming_active = False
        self.server_thread: Optional[threading.Thread] = None

        # Declare parameters
        self.declare_parameter('tactigon_mac', "C0:83:3D:34:25:38")
        self.declare_parameter('hand', "RIGHT")
        self.declare_parameter('host', '0.0.0.0')
        self.declare_parameter('port', 5013)
        self.declare_parameter('debug_tactigon', False)
        
        mac_address = self.get_parameter('tactigon_mac').get_parameter_value().string_value
        hand_str = self.get_parameter('hand').get_parameter_value().string_value
        self.host = self.get_parameter('host').get_parameter_value().string_value
        self.port = self.get_parameter('port').get_parameter_value().integer_value
        debug_mode = self.get_parameter('debug_tactigon').get_parameter_value().bool_value


        log_level = logging.DEBUG if debug_mode else logging.INFO
        logging.basicConfig(level=log_level, format='[%(levelname)s] [%(asctime)s] [%(name)s]: %(message)s')


        self.get_logger().info(f"Tactigon MAC: {mac_address}, Hand: {hand_str.upper()}")
        self.get_logger().info(f"TCP Server: {self.host}:{self.port}")
        if debug_mode:
             self.get_logger().info("Tactigon debug mode enabled.")

        try:
            hand = Hand[hand_str.upper()]
        except KeyError:
            self.get_logger().error(f"Invalid hand parameter: {hand_str}. Use RIGHT or LEFT.")
     
            raise ValueError("Invalid hand parameter") 

        tskin_cfg = TSkinConfig(mac_address, hand, gesture_config=None)
        
        try:
            self.tskin = TSkin_Audio(tskin_cfg, debug=debug_mode)
            self.tskin.start()
        except Exception as e:
            self.get_logger().error(f"Failed to initialize or start TSkin_Audio: {e}")
            
            return 

        self.get_logger().info("Waiting for TSkin to connect...")
        connect_timeout_sec = 30
        connect_start_time = time.time()
        connection_successful = False
        while time.time() - connect_start_time < connect_timeout_sec:
            if self.tskin.connected:
                connection_successful = True
                break
            if not rclpy.ok():
                self.get_logger().info("RCLPY shutdown requested during TSkin connection.")
                return # Early exit
            time.sleep(0.5)
        
        if not connection_successful:
            self.get_logger().error(f"TSkin connection timed out after {connect_timeout_sec} seconds.")
            # self.tskin might be partially initialized, let on_shutdown handle cleanup
            return # Early exit

        self.get_logger().info("TSkin connected.")

        self.server_thread = threading.Thread(target=self._server_loop, daemon=True)
        self.server_thread.start()

    def _server_loop(self):
        if not self.tskin: # Should not happen if __init__ completed successfully
            self.get_logger().error("TSkin not initialized, server loop cannot start.")
            return

        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen(1)
            self.get_logger().info(f"TCP Server listening on {self.host}:{self.port}")
        except Exception as e:
            self.get_logger().error(f"Failed to setup server socket: {e}")
            if self.server_socket: self.server_socket.close()
            self.server_socket = None
            return

        while rclpy.ok() and self.server_socket:
            self.get_logger().info("Waiting for a client connection...")
            try:
                self.server_socket.settimeout(1.0) # Check rclpy.ok() periodically
                self.client_connection, client_address = self.server_socket.accept()
            except socket.timeout:
                continue 
            except Exception as e:
                if rclpy.ok():
                    self.get_logger().error(f"Error accepting connection: {e}")
                break 

            self.get_logger().info(f"Client connected from {client_address}")
            self.streaming_active = True
            if self.tskin: # Ensure tskin is still valid
                self.tskin.listen()

            try:
                while rclpy.ok() and self.streaming_active and self.tskin and self.tskin._listen_event.is_set():
                    try:
                        frame = self.tskin.audio_data_queue.get(timeout=0.1)
                        if self.client_connection:
                           self.client_connection.sendall(frame)
                        else: 
                           self.streaming_active = False
                           break
                    except queue.Empty:
                        if not (self.tskin and self.tskin._listen_event.is_set()):
                             self.get_logger().info("TSkin stopped listening or invalid, ending stream for client.")
                             self.streaming_active = False
                        continue
                    except (socket.error, BrokenPipeError, ConnectionResetError) as e:
                        self.get_logger().warn(f"Client disconnected or socket error: {e}")
                        self.streaming_active = False
                        break 
                    except Exception as e:
                        self.get_logger().error(f"Error during streaming: {e}")
                        self.streaming_active = False
                        break
            finally:
                self.get_logger().info("Stopping audio stream to client.")
                if self.tskin:
                    self.tskin.stop_listen()

                if self.client_connection:
                    try:
                        self.client_connection.shutdown(socket.SHUT_RDWR)
                    except socket.error: pass
                    self.client_connection.close()
                    self.client_connection = None
                self.streaming_active = False
                self.get_logger().info("Client connection closed.")
        
        self.get_logger().info("Server loop ended.")
        if self.server_socket:
            self.server_socket.close()
            self.server_socket = None

    def on_shutdown(self):
        self.get_logger().info("Node shutdown requested.")
        self.streaming_active = False 

        
        if self.client_connection:
            try:
                self.client_connection.shutdown(socket.SHUT_RDWR)
            except (socket.error, OSError): pass #  for "transport endpoint is not connected"
            finally:
                try:
                    self.client_connection.close()
                except (socket.error, OSError): pass
                self.client_connection = None
        
        
        if self.server_socket:
            try:
                self.server_socket.close()
            except (socket.error, OSError): pass
            self.server_socket = None

        if self.server_thread and self.server_thread.is_alive():
            self.get_logger().info("Waiting for server thread to join...")
            self.server_thread.join(timeout=2.0)
            if self.server_thread.is_alive():
                self.get_logger().warning("Server thread did not join cleanly.")
        
        if self.tskin:
            self.get_logger().info("Joining TSkin...")
            try:
                self.tskin.join(timeout=5.0)
            except Exception as e:
                self.get_logger().error(f"Exception during tskin.join: {e}")
            self.tskin = None

        self.get_logger().info("Node shutdown sequence complete.")

def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = TactigonAudioStreamerNode()
        # Check if __init__ returned early due to an error (e.g., tskin not initialized)
        if node and node.tskin is None and node.server_thread is None: # Heuristic for early __init__ exit
             rclpy.logging.get_logger("main").error("Node initialization failed (likely TSkin), not spinning.")
        elif node:
            rclpy.spin(node)
    except KeyboardInterrupt:
        rclpy.logging.get_logger("main").info("Keyboard interrupt, shutting down")
    except ValueError as ve: # Catch specific errors like invalid hand
        rclpy.logging.get_logger("main").error(f"Configuration error: {ve}")
    except Exception as e:
        rclpy.logging.get_logger("main").fatal(f"Unnown exception in main: {e}", exc_info=True)
    finally:
        if node:
            node.on_shutdown()
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

if __name__ == '__main__':
    main()