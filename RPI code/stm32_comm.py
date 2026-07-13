import serial
import time
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

    def run(self):
        """Background loop listening for button presses."""
        try:
            # Open the serial port
            self.ser = serial.Serial(self.port, self.baudrate, timeout=0.1)
            self.status_received.emit(f"Connected to {self.port}")
            print(f"[UART] Listening on {self.port} at {self.baudrate} baud...")
            
            while self.is_running:
                # Check if data is waiting in the buffer
                if self.ser.in_waiting > 0:
                    raw_byte = self.ser.read(1)
                    
                    if raw_byte:
                        # Convert byte to integer and isolate the lower 4 bits
                        # e.g., 0xA5 (1010 0101) becomes 0x05 (0101)
                        val = raw_byte[0] & 0x0F 
                        
                        # Tell the GUI a button was pressed!
                        self.button_pressed.emit(val)
                        print(f"[UART] Received 4-bit command: {hex(val)}")
                else:
                    # Sleep briefly to keep CPU usage near 0%
                    time.sleep(0.01) 

        except Exception as e:
            self.status_received.emit("UART Error / Disconnected")
            print(f"[UART CRITICAL ERROR] {e}")

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
