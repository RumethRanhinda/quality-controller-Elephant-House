import time
import cv2
import numpy as np
import queue
from PyQt6.QtCore import QThread, pyqtSignal


class VisionThread(QThread):
    # Signals to communicate safely with the Main GUI
    frame_processed = pyqtSignal(np.ndarray, dict, bool, str)
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
        self.consecutive_failures = 0
        self.is_running = True

    def stop_pipeline(self):
        """Puts the OpenCV thread to sleep."""
        self.is_running = False

    def stop(self):
        """Kills the thread entirely on app exit."""
        self._stop_event = True
        self.quit()
        self.wait()

    def detect_date_code_presence(self, gray_frame):
        height, width = gray_frame.shape
        roi = gray_frame[int(height*0.2):int(height*0.85), int(width*0.2):int(width*0.8)]
        
        blurred = cv2.GaussianBlur(roi, (3, 3), 0)
        thresh = cv2.adaptiveThreshold(
            blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
            cv2.THRESH_BINARY_INV, 15, 5
        )
        
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        morph = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=1)
        
        # Calculate pixel density (ink presence)
        non_zero = cv2.countNonZero(morph)
        total_pixels = morph.shape[0] * morph.shape[1]
        density = (non_zero / total_pixels) * 100
        
        # Assuming density > 0.5% means ink is present (can be tuned later)
        is_present = density > 0.5
        return is_present, roi, morph

    def detect_fill_level(self, gray_frame):
        height, width = gray_frame.shape
        
        roi_top = int(height * 0.3)
        roi_bottom = int(height * 0.95)
        roi = gray_frame[roi_top:roi_bottom, int(width*0.1):int(width*0.9)]
        
        sobel_y = cv2.Sobel(roi, cv2.CV_64F, 0, 1, ksize=3)
        abs_sobel_y = cv2.convertScaleAbs(sobel_y)
        
        _, edges = cv2.threshold(abs_sobel_y, 50, 255, cv2.THRESH_BINARY)
        row_sums = np.sum(edges, axis=1)
        
        if len(row_sums) == 0 or np.max(row_sums) == 0:
            return None, roi, edges 
            
        relative_fill_y = np.argmax(row_sums)
        absolute_fill_y = roi_top + relative_fill_y
        
        return absolute_fill_y, roi, edges

    def process_image(self, gray_frame):
        """The core OpenCV algorithm using Sobel for fill and adaptive threshold for date code."""
        start_time = time.perf_counter()

        # 1. Date Code Check
        date_code_present, dc_roi, dc_morph = self.detect_date_code_presence(gray_frame)

        # 2. Fill Level Check
        fill_y, level_roi, level_edges = self.detect_fill_level(gray_frame)

        # 3. Pass/Fail Decision
        # Reject if UNDERFILLED or missing date code.
        is_underfilled = False
        if fill_y is None:
            is_underfilled = True
        else:
            # fill_y goes 0(top) to 1200(bottom).
            # If fill_y > target_threshold_y + tolerance, it is lower in the bottle (Underfilled)
            if fill_y > (self.target_threshold_y + self.tolerance_pixels):
                is_underfilled = True
        
        is_pass = (not is_underfilled) and date_code_present
        
        # Determine specific rejection reason if failed
        reason = ""
        if not is_pass:
            if not date_code_present and is_underfilled:
                reason = "Underfilled & No Date Code"
            elif not date_code_present:
                reason = "No Date Code"
            else:
                reason = "Underfilled"

        # Processing time
        proc_time_ms = (time.perf_counter() - start_time) * 1000

        # 4. Update Metrics
        self.metrics["total"] += 1
        self.metrics["proc_time_ms"] = proc_time_ms

        if is_pass:
            self.metrics["accepted"] += 1
            self.consecutive_failures = 0
            self.ejector_command.emit('P')
        else:
            self.metrics["rejected"] += 1
            self.consecutive_failures += 1
            self.ejector_command.emit('F')
            
            if self.consecutive_failures >= 5:
                time.sleep(0.04)  # 40ms delay to prevent STM32 UART overrun
                self.ejector_command.emit('B')

        self.metrics["yield"] = (self.metrics["accepted"] / self.metrics["total"]) * 100

        # 5. Create annotated image from the ORIGINAL frame
        annotated_img = cv2.cvtColor(gray_frame.copy(), cv2.COLOR_GRAY2BGR)
        height, width = gray_frame.shape

        # Draw ROI boxes
        # Date Code ROI
        cv2.rectangle(
            annotated_img,
            (int(width*0.2), int(height*0.2)),
            (int(width*0.8), int(height*0.85)),
            (0, 255, 0) if date_code_present else (0, 0, 255),
            2
        )
        # Fill Level ROI
        cv2.rectangle(
            annotated_img,
            (int(width*0.1), int(height*0.3)),
            (int(width*0.9), int(height*0.95)),
            (255, 255, 0),
            1
        )

        # Draw target fill line (Green)
        cv2.line(
            annotated_img,
            (0, self.target_threshold_y),
            (width, self.target_threshold_y),
            (0, 255, 0),
            2,
        )

        # Draw detected fill line
        if fill_y is not None:
            line_color = (0, 255, 0) if not is_underfilled else (0, 0, 255)
            cv2.line(
                annotated_img,
                (0, fill_y),
                (width, fill_y),
                line_color,
                3,
            )

        return annotated_img, is_pass, reason

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
                    annotated_img, is_pass, reason = self.process_image(
                        gray_frame
                    )

                    # Send updated metrics
                    self.metrics_ready.emit(self.metrics)

                    # Report the processed frame directly to the GUI (Pass or Fail)
                    self.frame_processed.emit(
                        annotated_img,
                        self.metrics,
                        is_pass,
                        reason
                    )

                except queue.Empty:
                    # No bottle available
                    pass

            else:
                time.sleep(0.05)

    @staticmethod
    def generate_preview(size, flavour, threshold):
        """Generates a static dummy image for the GUI Setup screen preview (1920x1200)."""
        img = np.ones((1200, 1920, 3), dtype=np.uint8) * 40

        # Draw threshold line
        cv2.line(
            img,
            (100, threshold),
            (1820, threshold),
            (0, 255, 0),
            4
        )

        cv2.putText(
            img,
            f"Simulated Camera View: {size} {flavour}",
            (150, 100),
            cv2.FONT_HERSHEY_SIMPLEX,
            2.0,
            (255, 255, 255),
            4,
        )

        cv2.putText(
            img,
            f"Target: {threshold}px",
            (150, threshold - 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.5,
            (0, 255, 0),
            3,
        )

        return img
