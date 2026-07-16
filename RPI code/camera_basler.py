import queue
import time
import threading
import numpy as np
from pypylon import pylon
from pypylon import genicam
from PyQt6.QtCore import QThread, pyqtSignal

class CameraThread(QThread):
    status_changed = pyqtSignal(str)
    
    def __init__(self, raw_queue):
        """
        Initializes the Camera Thread.
        :param raw_queue: A queue.Queue() object to push captured frames into.
        """
        super().__init__()
        self.raw_queue = raw_queue
        
        # State Flags
        self.is_running = False      # True when "Start Run", False when "Home"
        self._stop_event = threading.Event() # Used to completely kill the thread on exit
        
        # Camera Object
        self.camera = None

    def setup_hardware_trigger(self):
        """Configures the camera to wait for the STM32's 3.3V pulse on Pin 1."""
        try:
            # 1. Reset to factory defaults so we have a clean slate
            self.camera.UserSetSelector.Value = "Default"
            self.camera.UserSetLoad.Execute()

            # 2. Set Pixel Format to Monochrome 8-bit (1 byte per pixel)
            self.camera.PixelFormat.Value = "Mono8"

            # 3. Configure the Hardware Trigger
            self.camera.TriggerSelector.Value = "FrameStart"
            self.camera.TriggerMode.Value = "On"
            
            # "Line1" is the physical opto-coupled GPIO input pin on the Basler camera
            # Set to "Software" for testing. Normally this should be "Line1" for hardware GPIO.
            self.camera.TriggerSource.Value = "Software" 
            # self.camera.TriggerActivation.Value = "RisingEdge" # Only used when TriggerSource is "Line1"

            # 4. Set Shutter Speed (Exposure Time in microseconds)
            # Use the currently active exposure (defaults to 3000 if not in a run)
            self.camera.ExposureTime.Value = getattr(self, 'current_exposure', 3000)

            print("[CAMERA] Hardware Trigger Configured: Waiting for STM32 pulse on Line 1.")

        except genicam.GenericException as e:
            print(f"[CAMERA ERROR] Failed to configure camera: {e}")

    def run(self):
        """The main loop that runs continuously on Core 1 of the Pi."""
        tl_factory = pylon.TlFactory.GetInstance()
        
        while not self._stop_event.is_set():
            if self.camera is None or not self.camera.IsOpen():
                try:
                    self.status_changed.emit("Camera: Disconnected - Searching...")
                    devices = tl_factory.EnumerateDevices()
                    if not devices:
                        time.sleep(1)
                        continue
                        
                    self.camera = pylon.InstantCamera(tl_factory.CreateDevice(devices[0]))
                    self.camera.Open()
                    self.setup_hardware_trigger()
                    self.camera.StartGrabbing(pylon.GrabStrategy_OneByOne)
                    self.status_changed.emit("Camera: Connected & Ready")
                    print("[CAMERA] Ready and idling.")
                except genicam.GenericException as e:
                    self.status_changed.emit("Camera: Connection Error")
                    if self.camera is not None and self.camera.IsOpen():
                        self.camera.Close()
                    self.camera = None
                    time.sleep(1)
                    continue
            
            try:
                if self.is_running:
                    # --- TESTING MODE (SOFTWARE TRIGGER) ---
                    if self.camera.TriggerSource.Value == "Software":
                        if self.camera.WaitForFrameTriggerReady(100, pylon.TimeoutHandling_Return):
                            self.camera.ExecuteSoftwareTrigger()
                            time.sleep(0.5) 
                    
                    grabResult = self.camera.RetrieveResult(100, pylon.TimeoutHandling_Return)
                    
                    if grabResult.GrabSucceeded():
                        img_array = grabResult.Array
                        self.raw_queue.put(img_array.copy())
                        
                    grabResult.Release()
                else:
                    time.sleep(0.05)
                    try:
                        if self.camera.IsCameraDeviceRemoved():
                            raise genicam.GenericException("Device removed")
                    except AttributeError:
                        # Fallback for older pypylon versions
                        _ = self.camera.Width.GetValue()
            except genicam.GenericException as e:
                self.status_changed.emit("Camera: Disconnected unexpectedly!")
                if self.camera is not None:
                    if self.camera.IsGrabbing():
                        self.camera.StopGrabbing()
                    if self.camera.IsOpen():
                        self.camera.Close()
                self.camera = None
                
        # Cleanup
        if self.camera is not None:
            if self.camera.IsGrabbing():
                self.camera.StopGrabbing()
            if self.camera.IsOpen():
                self.camera.Close()
        print("[CAMERA] Disconnected and closed safely.")

    def start_run(self, exposure_us=3000):
        """Called by main_gui.py to wake the camera up."""
        try:
            if self.camera is not None:
                self.camera.ExposureTime.Value = exposure_us
        except Exception as e:
            print(f"[CAMERA ERROR] Failed to set exposure: {e}")
        self.is_running = True

    def stop_run(self):
        """Called by main_gui.py to put the camera to sleep."""
        self.is_running = False

    def shutdown(self):
        """Called by main_gui.py on system exit."""
        self._stop_event.set()
