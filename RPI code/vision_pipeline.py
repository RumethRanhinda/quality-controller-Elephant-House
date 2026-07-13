import time
import cv2
import numpy as np
import queue
from PyQt6.QtCore import QThread, pyqtSignal


class VisionThread(QThread):
    # Signals to communicate safely with the Main GUI
    rejection_occurred = pyqtSignal(np.ndarray, dict, str)
    metrics_ready = pyqtSignal(dict)
    trigger_received = pyqtSignal()

    # Signal to communicate directly with the STM32 UART thread
    # Emits 'P' for Pass, 'F' for Fail
    ejector_command = pyqtSignal(str)

    def __init__(self, raw_queue):
        super().__init__()
        self.raw_queue = raw_queue
        self.is_running = False
        self._stop_event = False

        # Current active configuration
        self.current_size = "500ml"
        self.current_flavour = "Kik Cola"
        self.target_threshold_y = 300  # Pixel Y-coordinate for perfect fill
        self.tolerance_pixels = 15      # Allowable +/- variance

        # Session Metrics
        self.metrics = {
            "total": 0,
            "rejected": 0,
            "accepted": 0,
            "yield": 100.0,
            "proc_time_ms": 0.0
        }

    def update_config(self, size, flavour, threshold):
        """Called by the GUI to push JSON settings into the active vision engine."""
        self.current_size = size
        self.current_flavour = flavour
        self.target_threshold_y = threshold

    def start_pipeline(self):
        """Wakes up the OpenCV thread."""
        self.metrics = {
            "total": 0,
            "rejected": 0,
            "accepted": 0,
            "yield": 100.0,
            "proc_time_ms": 0.0
        }
        self.is_running = True

    def stop_pipeline(self):
        """Puts the OpenCV thread to sleep."""
        self.is_running = False

    def stop(self):
        """Kills the thread entirely on app exit."""
        self._stop_event = True
        self.quit()
        self.wait()

    def process_image(self, gray_frame):
        """The core OpenCV algorithm - No Gaussian Blur."""

        start_time = time.perf_counter()

        # 1. Perform Canny edge detection directly on the original image
        edges = cv2.Canny(gray_frame, 40, 120)

        # 2. Find the strongest horizontal edge (fill line)
        row_edge_counts = np.sum(edges, axis=1)
        found_line_y = np.argmax(row_edge_counts)

        # 3. Pass/Fail Decision
        error_distance = abs(found_line_y - self.target_threshold_y)
        is_pass = error_distance <= self.tolerance_pixels

        # Processing time
        proc_time_ms = (time.perf_counter() - start_time) * 1000

        # 4. Update Metrics
        self.metrics["total"] += 1
        self.metrics["proc_time_ms"] = proc_time_ms

        if is_pass:
            self.metrics["accepted"] += 1
            self.ejector_command.emit('P')
        else:
            self.metrics["rejected"] += 1
            self.ejector_command.emit('F')

        self.metrics["yield"] = (
            self.metrics["accepted"] / self.metrics["total"]
        ) * 100

        # 5. Create annotated image from the ORIGINAL frame
        annotated_img = cv2.cvtColor(gray_frame.copy(), cv2.COLOR_GRAY2BGR)

        # Draw target line (Green)
        cv2.line(
            annotated_img,
            (0, self.target_threshold_y),
            (annotated_img.shape[1], self.target_threshold_y),
            (0, 255, 0),
            2,
        )

        # Draw detected fill line
        line_color = (255, 0, 0) if is_pass else (0, 0, 255)
        cv2.line(
            annotated_img,
            (0, found_line_y),
            (annotated_img.shape[1], found_line_y),
            line_color,
            3,
        )

        return annotated_img, is_pass, found_line_y

    def run(self):
        """The continuous background loop."""
        while not self._stop_event:

            if self.is_running:
                try:
                    # Wait for an image from the Basler Camera thread
                    gray_frame = self.raw_queue.get(timeout=0.1)

                    # Notify GUI a trigger was received
                    self.trigger_received.emit()

                    # Process image
                    annotated_img, is_pass, found_line_y = self.process_image(
                        gray_frame
                    )

                    # Send updated metrics
                    self.metrics_ready.emit(self.metrics)

                    # Report rejection if necessary
                    if not is_pass:
                        if found_line_y < self.target_threshold_y:
                            reason = "Overfilled"
                        else:
                            reason = "Underfilled"

                        self.rejection_occurred.emit(
                            annotated_img,
                            self.metrics,
                            reason
                        )

                except queue.Empty:
                    # No bottle available
                    pass

            else:
                time.sleep(0.05)

    @staticmethod
    def generate_preview(size, flavour, threshold):
        """Generates a static dummy image for the GUI Setup screen preview."""

        img = np.ones((600, 800, 3), dtype=np.uint8) * 40

        # Draw threshold line
        cv2.line(
            img,
            (100, threshold),
            (700, threshold),
            (0, 255, 0),
            2
        )

        cv2.putText(
            img,
            f"Simulated Camera View: {size} {flavour}",
            (150, 50),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (255, 255, 255),
            2,
        )

        cv2.putText(
            img,
            f"Target: {threshold}px",
            (150, threshold - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 255, 0),
            2,
        )

        return img
