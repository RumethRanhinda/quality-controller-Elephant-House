import serial
import time
import socket
from PyQt6.QtCore import QThread, pyqtSignal

class SerialThread(QThread):
    # Signals to talk to the GUI
    status_received = pyqtSignal(str) 
    button_pressed = pyqtSignal(int)  

    def __init__(self, port='/dev/ttyAMA0', baudrate=115200):
        super().__init__()
        self.port = port
        self.baudrate = baudrate
        self.is_running = True
        self.ser = None
        self.sock = None

    def run(self):
        """Background loop listening for button presses."""
        try:
            # Open the serial port
            self.ser = serial.Serial(self.port, self.baudrate, timeout=0.1)
            self.status_received.emit(f"Connected to {self.port}")
            print(f"[UART] Listening on {self.port} at {self.baudrate} baud...")
        except serial.SerialException as e:
            print(f"[UART] Failed to open {self.port}: {e}")
            print(f"[UART] Falling back to UDP Simulator on port 12345...")
            self.status_received.emit("Connected (Simulated UDP)")
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.bind(('127.0.0.1', 12345))
            self.sock.settimeout(0.1)
            
        while self.is_running:
            if self.ser and self.ser.is_open:
                # Check if data is waiting in the buffer
                if self.ser.in_waiting > 0:
                    raw_byte = self.ser.read(1)
                    
                    if raw_byte:
                        val = raw_byte[0] & 0x0F 
                        self.button_pressed.emit(val)
                        print(f"[UART] Received 4-bit command: {hex(val)}")
                else:
                    time.sleep(0.01) 
            elif self.sock:
                try:
                    data, _ = self.sock.recvfrom(1024)
                    if data:
                        char = data.decode('utf-8').strip()
                        if char.isdigit():
                            val = int(char)
                            self.button_pressed.emit(val)
                            print(f"[SIMULATOR] Received: {val}")
                except socket.timeout:
                    pass
            else:
                time.sleep(0.1)

    def send_ejector_command(self, cmd_char):
        """Called by Vision Thread to send 'P' or 'F' to the STM32."""
        if self.ser and self.ser.is_open:
            self.ser.write(cmd_char.encode('utf-8'))

    def stop(self):
        self.is_running = False
        self.quit()
        self.wait()
        if self.ser and self.ser.is_open:
            self.ser.close()
