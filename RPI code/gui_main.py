import sys
import os
import json
import time
import queue
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QComboBox, QSlider, 
                             QPushButton, QGroupBox, QFormLayout, QStackedWidget,
                             QListWidget, QListWidgetItem, QSplitter, QSizePolicy)
from PyQt6.QtCore import Qt, pyqtSlot, QTimer, QEvent, QObject
from PyQt6.QtGui import QImage, QPixmap, QKeyEvent
import cv2
import numpy as np

# Import other modules
from vision_pipeline import VisionThread
from stm32_comm import SerialThread
from camera_basler import CameraThread
from data_manager import DataManager

class OperatorDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("EDR Project - Industrial Quality Control")
        self.resize(1024, 768)

        # Style sheet to provide highly visible orange focus highlights for physical switchboard controls
        # Bold fonts used for titles, section titles, and button labels only
        self.setStyleSheet("""
            QWidget {
                background-color: #1a1a1a;
                color: #ffffff;
            }
            QPushButton:focus, QComboBox:focus, QSlider:focus, QListWidget:focus {
                border: 4px solid #f39c12;
                outline: none;
            }
            QPushButton {
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 10px;
                font-weight: bold;
                font-size: 16px;
            }
            QGroupBox {
                font-weight: bold;
                font-size: 18px;
                margin-top: 15px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px 0 3px;
            }
            QLabel {
                font-weight: normal;
                font-size: 14px;
            }
            .TopicLabel {
                font-weight: bold;
                font-size: 28px;
                color: #ecf0f1;
                margin-bottom: 5px;
            }
            QComboBox {
                background-color: #2a2a2a;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 6px 30px 6px 6px;
                color: white;
                font-weight: normal;
                font-size: 14px;
            }
            QComboBox:focus {
                background-color: #2c3e50;
                border: 4px solid #f39c12;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: #444444;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #aaaaaa;
                width: 18px;
                height: 18px;
                margin: -6px 0;
                border-radius: 9px;
            }
            QSlider::handle:horizontal:focus {
                background: #f39c12;
            }
            QSlider::sub-page:horizontal {
                background: #2980b9;
                border-radius: 3px;
            }
            QListWidget {
                background-color: #222222;
                border: 1px solid #444444;
                font-weight: normal;
                font-size: 14px;
            }
            QListWidget::item:selected {
                background-color: #34495e;
                color: white;
            }
            QListWidget::item:focus {
                border: 2px solid #f39c12;
            }
        """)

        # Load databases via DataManager
        self.data_manager = DataManager()
        self.history = self.data_manager.load_history()
        self.bottle_configs = self.data_manager.load_bottle_configs()

        # Ensure assets directory exists for storing reference photos
        os.makedirs("assets", exist_ok=True)

        # Thread instances
        self.raw_queue = queue.Queue()
        self.camera_thread = CameraThread(self.raw_queue)
        self.vision_thread = VisionThread(self.raw_queue)
        self.serial_thread = SerialThread()

        # Connect vision signals
        self.vision_thread.frame_processed.connect(self.on_frame_processed)
        self.vision_thread.metrics_ready.connect(self.on_metrics_ready)
        self.vision_thread.ejector_command.connect(self.serial_thread.send_ejector_command)
        
        # Connect hardware status signals
        self.serial_thread.status_received.connect(self.update_serial_status)
        self.serial_thread.button_pressed.connect(self.on_hardware_button_pressed)
        self.camera_thread.status_changed.connect(self.update_camera_status)

        # Start background threads
        self.camera_thread.start()
        self.vision_thread.start()
        self.serial_thread.start()

        self.session_start_time = 0.0
        self.current_metrics = {"total": 0, "accepted": 0, "rejected": 0, "yield": 100.0, "proc_time_ms": 0.0}
        
        self.stm32_ready = False
        self.camera_ready = False
        
        self.last_trigger_time = 0.0
        self.watchdog_timer = QTimer()
        self.watchdog_timer.timeout.connect(self.check_end_button_status)
        self.vision_thread.trigger_received.connect(self.on_trigger_received)
        
        # Stacked widget for switching between Main Dashboard, History, and Setup Screens
        self.stacked_widget = QStackedWidget()
        self.setCentralWidget(self.stacked_widget)

        # State management
        self.ui_state = "HOME"

        self.init_main_dashboard()
        self.init_history_screen()
        self.init_setup_screen()

        self.stacked_widget.addWidget(self.dashboard_widget) # Index 0
        self.stacked_widget.addWidget(self.history_widget)   # Index 1
        self.stacked_widget.addWidget(self.setup_widget)     # Index 2

        # Transition to initial state
        self.transition_to_state("HOME")

    # --- UI Initialization ---
    def init_main_dashboard(self):
        self.dashboard_widget = QWidget()
        layout = QHBoxLayout(self.dashboard_widget)

        # --- LEFT PANEL ---
        self.left_panel = QVBoxLayout()
        
        self.left_title = QLabel("Configuration Preview")
        self.left_title.setProperty("class", "TopicLabel")
        self.left_panel.addWidget(self.left_title)

        # Left display (Preview image / rejection logs) - Fixed size (16:10 ratio) to prevent layout growing
        self.left_display = QLabel()
        self.left_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.left_display.setFixedSize(512, 320)
        self.left_display.setStyleSheet("background-color: #1e1e1e; border: 2px solid #333333;")
        self.left_panel.addWidget(self.left_display)

        # Comments/Status log
        self.status_comment = QLabel("")
        self.status_comment.setStyleSheet("font-size: 16px; font-weight: bold; color: #ff5555;")
        self.status_comment.setWordWrap(True)
        self.left_panel.addWidget(self.status_comment)

        # Home screen statistics container
        self.home_stats_group = QGroupBox("Summary of Previous Run")
        stats_layout = QFormLayout()
        self.lbl_last_size = QLabel("-")
        self.lbl_last_flavour = QLabel("-")
        self.lbl_last_total = QLabel("-")
        self.lbl_last_rejects = QLabel("-")
        self.lbl_last_yield = QLabel("-")
        
        stats_layout.addRow("Bottle Size:", self.lbl_last_size)
        stats_layout.addRow("Flavour:", self.lbl_last_flavour)
        stats_layout.addRow("Total Checked:", self.lbl_last_total)
        stats_layout.addRow("Rejected Count:", self.lbl_last_rejects)
        stats_layout.addRow("Yield Ratio:", self.lbl_last_yield)
        self.home_stats_group.setLayout(stats_layout)
        self.left_panel.addWidget(self.home_stats_group)

        self.left_panel.addStretch()

        layout.addLayout(self.left_panel)

        # --- RIGHT PANEL (CONFIG & CONTROLS) ---
        self.right_panel = QVBoxLayout()
        
        self.config_group = QGroupBox("Product Selection")
        config_form = QFormLayout()

        # Bottle Size Dropdown
        self.combo_size = QComboBox()
        self.combo_size.addItems(["500ml", "1l", "1.5l"])
        self.combo_size.currentTextChanged.connect(self.on_home_product_changed)

        # Flavour Dropdown
        self.combo_flavour = QComboBox()
        self.combo_flavour.addItems([
            "Cream Soda",
            "Dry Ginger Ale",
            "Ginger Beer",
            "KIK Cola",
            "Lemonade",
            "Necto",
            "Orange Barley",
            "Orange Crush",
            "Soda",
            "Tonic"
        ])
        self.combo_flavour.currentTextChanged.connect(self.on_home_product_changed)

        # Limit Value Text Display Only (No slider on Home)
        self.lbl_threshold_val = QLabel("300 px")
        self.lbl_threshold_val.setStyleSheet("color: #e67e22; font-weight: bold;")

        config_form.addRow("Bottle Size:", self.combo_size)
        config_form.addRow("Flavour:", self.combo_flavour)
        config_form.addRow("Target Fill Level:", self.lbl_threshold_val)
        
        self.config_group.setLayout(config_form)
        self.right_panel.addWidget(self.config_group)

        # Live Metrics Panel
        self.running_metrics_group = QGroupBox("Current Batch Metrics")
        run_layout = QFormLayout()
        self.lbl_run_total = QLabel("0")
        self.lbl_run_accepted = QLabel("0")
        self.lbl_run_rejected = QLabel("0")
        self.lbl_run_yield = QLabel("100.0 %")
        
        run_layout.addRow("Total Checked:", self.lbl_run_total)
        run_layout.addRow("Accepted Bottles:", self.lbl_run_accepted)
        run_layout.addRow("Rejected Bottles:", self.lbl_run_rejected)
        run_layout.addRow("Yield Ratio:", self.lbl_run_yield)
        self.running_metrics_group.setLayout(run_layout)
        self.right_panel.addWidget(self.running_metrics_group)

        # Controller Connection Status
        self.serial_group = QGroupBox("Hardware Status")
        serial_lay = QVBoxLayout()
        self.lbl_serial = QLabel("STM32: Initializing...")
        self.lbl_camera = QLabel("Camera: Initializing...")
        serial_lay.addWidget(self.lbl_serial)
        serial_lay.addWidget(self.lbl_camera)
        self.serial_group.setLayout(serial_lay)
        self.right_panel.addWidget(self.serial_group)

        self.right_panel.addStretch()

        # History button
        self.btn_go_history = QPushButton("View Session History")
        self.btn_go_history.setStyleSheet("background-color: #34495e; color: white;")
        self.btn_go_history.clicked.connect(self.go_to_history_screen)
        self.right_panel.addWidget(self.btn_go_history)
        
        # Control Buttons
        self.btn_setup = QPushButton("Configure Setup")
        self.btn_setup.setStyleSheet("background-color: #d35400; color: white;") 
        self.btn_setup.clicked.connect(self.go_to_setup_screen)
        self.right_panel.addWidget(self.btn_setup)
        
        self.btn_start = QPushButton("Start Run")
        self.btn_start.setStyleSheet("background-color: #27ae60; color: white;")
        self.btn_start.clicked.connect(self.on_start_clicked)
        self.right_panel.addWidget(self.btn_start)

        self.btn_end = QPushButton("End Run")
        self.btn_end.setStyleSheet("background-color: #c0392b; color: white;")
        self.btn_end.clicked.connect(self.on_end_clicked)
        self.right_panel.addWidget(self.btn_end)

        layout.addLayout(self.right_panel)

    def init_history_screen(self):
        self.history_widget = QWidget()
        layout = QVBoxLayout(self.history_widget)

        title = QLabel("Historical Inspection Sessions")
        title.setProperty("class", "TopicLabel")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        
        lbl_instruction = QLabel("(Use Left/Right buttons to scroll through records)")
        lbl_instruction.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_instruction.setStyleSheet("color: #aaaaaa; font-style: italic;")
        layout.addWidget(lbl_instruction)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        
        self.history_list = QListWidget()
        self.history_list.itemSelectionChanged.connect(self.on_history_selection_changed)
        splitter.addWidget(self.history_list)

        # Details list
        self.detail_pane = QGroupBox("Session Record Details")
        detail_layout = QFormLayout()
        
        self.lbl_hist_time = QLabel("-")
        self.lbl_hist_endtime = QLabel("-")
        self.lbl_hist_size = QLabel("-")
        self.lbl_hist_flavour = QLabel("-")
        self.lbl_hist_total = QLabel("-")
        self.lbl_hist_accepted = QLabel("-")
        self.lbl_hist_rejected = QLabel("-")
        self.lbl_hist_yield = QLabel("-")
        self.lbl_hist_duration = QLabel("-")
        self.lbl_hist_threshold = QLabel("-")

        detail_layout.addRow("Start Time:", self.lbl_hist_time)
        detail_layout.addRow("End Time:", self.lbl_hist_endtime)
        detail_layout.addRow("Bottle Size:", self.lbl_hist_size)
        detail_layout.addRow("Flavour:", self.lbl_hist_flavour)
        detail_layout.addRow("Total Checked:", self.lbl_hist_total)
        detail_layout.addRow("Accepted Bottles:", self.lbl_hist_accepted)
        detail_layout.addRow("Rejected Bottles:", self.lbl_hist_rejected)
        detail_layout.addRow("Yield Ratio:", self.lbl_hist_yield)
        detail_layout.addRow("Run Duration:", self.lbl_hist_duration)
        detail_layout.addRow("Fill Limit (px):", self.lbl_hist_threshold)

        self.detail_pane.setLayout(detail_layout)
        splitter.addWidget(self.detail_pane)
        
        splitter.setSizes([350, 450])
        layout.addWidget(splitter)
        
        bottom_layout = QHBoxLayout()
        bottom_layout.addStretch()
        self.btn_hist_back = QPushButton("← Back to Home")
        self.btn_hist_back.setStyleSheet("background-color: #7f8c8d; color: white; max-width: 250px;")
        self.btn_hist_back.clicked.connect(self.go_to_home_screen)
        bottom_layout.addWidget(self.btn_hist_back)
        layout.addLayout(bottom_layout)

    def init_setup_screen(self):
        self.setup_widget = QWidget()
        layout = QHBoxLayout(self.setup_widget)

        # --- LEFT PANEL ---
        self.setup_left_panel = QVBoxLayout()
        
        self.setup_title = QLabel("Setup Configuration")
        self.setup_title.setProperty("class", "TopicLabel")
        self.setup_left_panel.addWidget(self.setup_title)

        # Setup preview visual label - Fixed size (16:10 ratio)
        self.setup_display = QLabel()
        self.setup_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setup_display.setFixedSize(512, 320)
        self.setup_display.setStyleSheet("background-color: #1e1e1e; border: 2px solid #333333;")
        self.setup_left_panel.addWidget(self.setup_display)
        
        self.setup_left_panel.addStretch()
        layout.addLayout(self.setup_left_panel)

        # --- RIGHT PANEL ---
        self.setup_right_panel = QVBoxLayout()
        
        self.setup_config_group = QGroupBox("Target Adjustment Setup")
        setup_form = QFormLayout()

        # Selection variables in Setup
        self.combo_setup_size = QComboBox()
        self.combo_setup_size.addItems(["500ml", "1l", "1.5l"])
        self.combo_setup_size.currentTextChanged.connect(self.on_setup_visual_update)

        self.combo_setup_flavour = QComboBox()
        self.combo_setup_flavour.addItems([
            "Cream Soda",
            "Dry Ginger Ale",
            "Ginger Beer",
            "KIK Cola",
            "Lemonade",
            "Necto",
            "Orange Barley",
            "Orange Crush",
            "Soda",
            "Tonic"
        ])
        self.combo_setup_flavour.currentTextChanged.connect(self.on_setup_visual_update)

        self.slider_setup_threshold = QSlider(Qt.Orientation.Horizontal)
        self.slider_setup_threshold.setRange(0, 1200)
        self.slider_setup_threshold.setValue(300)
        self.slider_setup_threshold.valueChanged.connect(self.on_setup_visual_update)

        self.lbl_setup_threshold_val = QLabel("300 px")
        self.lbl_setup_threshold_val.setStyleSheet("color: #e67e22; font-weight: bold;")

        setup_form.addRow("Bottle Size:", self.combo_setup_size)
        setup_form.addRow("Flavour:", self.combo_setup_flavour)
        setup_form.addRow("Adjust Fill Limit:", self.slider_setup_threshold)
        setup_form.addRow("Limit Value:", self.lbl_setup_threshold_val)
        
        self.setup_config_group.setLayout(setup_form)
        self.setup_right_panel.addWidget(self.setup_config_group)

        self.setup_right_panel.addStretch()

        # Setup buttons
        self.btn_setup_save = QPushButton("Save Specification")
        self.btn_setup_save.setStyleSheet("background-color: #27ae60; color: white;")
        self.btn_setup_save.clicked.connect(self.on_setup_save_clicked)
        
        self.btn_setup_back = QPushButton("← Cancel & Go Back")
        self.btn_setup_back.setStyleSheet("background-color: #7f8c8d; color: white;")
        self.btn_setup_back.clicked.connect(self.on_setup_cancel_clicked)

        self.setup_right_panel.addWidget(self.btn_setup_save)
        self.setup_right_panel.addWidget(self.btn_setup_back)

        layout.addLayout(self.setup_right_panel)

    # --- Home screen preview drawer ---
    def get_reference_preview(self, size, flavour, threshold):
        """Loads a static reference photo from assets/ if it exists; otherwise generates a dummy preview."""
        png_path = f"assets/{size}_{flavour}.png"
        jpg_path = f"assets/{size}_{flavour}.jpg"
        
        target_path = None
        if os.path.exists(png_path):
            target_path = png_path
        elif os.path.exists(jpg_path):
            target_path = jpg_path

        if target_path:
            img = cv2.imread(target_path)
            if img is not None:
                # Resize to standard size 1920x1200 for consistency
                img = cv2.resize(img, (1920, 1200))
                # Draw the green target line on the 1920x1200 canvas
                cv2.line(img, (100, threshold), (1820, threshold), (0, 255, 0), 4)
                cv2.putText(
                    img,
                    f"Reference Photo: {size} {flavour}",
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

        # Fallback to generated preview if file not found or load fails
        return VisionThread.generate_preview(size, flavour, threshold)

    def show_home_preview(self, size, flavour, threshold):
        """Draws the preview of the locked active configuration on the Home screen."""
        preview_img = self.get_reference_preview(size, flavour, threshold)
        
        # Display it on main feed screen
        rgb_image = cv2.cvtColor(preview_img, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_image.shape
        bytes_per_line = ch * w
        qt_img = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
        scaled_pixmap = QPixmap.fromImage(qt_img).scaled(
            512, 320, 
            Qt.AspectRatioMode.KeepAspectRatio, 
            Qt.TransformationMode.SmoothTransformation
        )
        self.left_display.setPixmap(scaled_pixmap)

    # --- UI State Controller ---
    def transition_to_state(self, state):
        self.ui_state = state

        if state == "HOME":
            self.left_title.setText("Configuration Preview")
            self.status_comment.clear()
            self.home_stats_group.show()
            self.btn_go_history.show()
            
            # Load last session if it exists
            if self.history:
                last_session = self.history[0]
                self.lbl_last_size.setText(last_session.get("bottle_size", "-"))
                self.lbl_last_flavour.setText(last_session.get("flavour", "-"))
                self.lbl_last_total.setText(str(last_session.get("total", "-")))
                self.lbl_last_rejects.setText(str(last_session.get("rejected", "-")))
                self.lbl_last_yield.setText(f"{last_session.get('yield', 100.0):.1f} %")
            else:
                self.lbl_last_size.setText("-")
                self.lbl_last_flavour.setText("-")
                self.lbl_last_total.setText("-")
                self.lbl_last_rejects.setText("-")
                self.lbl_last_yield.setText("-")

            # Enable selection controls on Home
            self.combo_size.setEnabled(True)
            self.combo_flavour.setEnabled(True)
            self.running_metrics_group.hide()

            # Buttons visibility
            self.btn_setup.show()
            self.btn_start.show()
            self.btn_end.hide()
            self.watchdog_timer.stop()
            self.check_start_button_status()

            self.on_home_product_changed()

        elif state == "RUNNING":
            # Reset metrics representation
            self.lbl_run_total.setText("0")
            self.lbl_run_accepted.setText("0")
            self.lbl_run_rejected.setText("0")
            self.lbl_run_yield.setText("100.0 %")

            self.left_title.setText("Live Rejection Log")
            self.left_display.clear()
            self.left_display.setText("Monitoring line... No rejections logged yet.")
            self.home_stats_group.hide()
            self.btn_go_history.hide()
            self.status_comment.clear()

            # Disable selection controls during execution
            self.combo_size.setEnabled(False)
            self.combo_flavour.setEnabled(False)
            self.running_metrics_group.show()

            # Buttons visibility
            self.btn_setup.hide()
            self.btn_start.hide()
            
            self.btn_end.show()
            self.btn_end.setEnabled(False)
            self.btn_end.setText("End Run (Locked)")
            self.btn_end.setStyleSheet("background-color: #552222; color: #888888;")

            # Sync values to vision thread & run pipeline
            size = self.combo_size.currentText()
            flavour = self.combo_flavour.currentText()
            key = f"{size}_{flavour}"
            threshold = self.get_threshold_for(key)
            exposure = self.get_exposure_for(key)
            
            self.vision_thread.update_config(size, flavour, threshold)
            
            self.camera_thread.start_run(exposure)
            self.vision_thread.start_pipeline()

            # Initialize timestamps
            self.session_start_time = time.time()
            self.last_trigger_time = time.time()
            self.watchdog_timer.start(100)

    # --- Actions / Callbacks ---
    @pyqtSlot()
    def on_home_product_changed(self):
        """Loads threshold for selected bottle on Home screen and updates preview."""
        if self.ui_state != "HOME":
            return
        size = self.combo_size.currentText()
        flavour = self.combo_flavour.currentText()
        key = f"{size}_{flavour}"
        threshold = self.get_threshold_for(key)
        self.lbl_threshold_val.setText(f"{threshold} px")
        self.show_home_preview(size, flavour, threshold)

    @pyqtSlot()
    def on_setup_visual_update(self):
        """Redraws the bottle preview when settings change in Setup screen."""
        if self.stacked_widget.currentIndex() != 2:
            return
            
        size = self.combo_setup_size.currentText()
        flavour = self.combo_setup_flavour.currentText()
        
        if self.sender() in [self.combo_setup_size, self.combo_setup_flavour]:
            key = f"{size}_{flavour}"
            threshold = self.get_threshold_for(key)
            self.slider_setup_threshold.setValue(threshold)
        else:
            threshold = int(self.slider_setup_threshold.value())

        self.lbl_setup_threshold_val.setText(f"{threshold} px")

        # Load/Generate setup preview image
        preview_img = self.get_reference_preview(size, flavour, threshold)
        
        # Display it on Setup Screen label
        rgb_image = cv2.cvtColor(preview_img, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_image.shape
        bytes_per_line = ch * w
        qt_img = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
        scaled_pixmap = QPixmap.fromImage(qt_img).scaled(
            512, 320, 
            Qt.AspectRatioMode.KeepAspectRatio, 
            Qt.TransformationMode.SmoothTransformation
        )
        self.setup_display.setPixmap(scaled_pixmap)

    @pyqtSlot()
    def go_to_setup_screen(self):
        self.combo_setup_size.setCurrentText(self.combo_size.currentText())
        self.combo_setup_flavour.setCurrentText(self.combo_flavour.currentText())
        
        key = f"{self.combo_size.currentText()}_{self.combo_flavour.currentText()}"
        threshold = self.get_threshold_for(key)
        self.slider_setup_threshold.setValue(threshold)
        
        self.on_setup_visual_update()
        self.stacked_widget.setCurrentIndex(2)
        self.combo_setup_size.setFocus()

    @pyqtSlot()
    def on_setup_save_clicked(self):
        size = self.combo_setup_size.currentText()
        flavour = self.combo_setup_flavour.currentText()
        threshold = self.slider_setup_threshold.value()
        
        key = f"{size}_{flavour}"
        if isinstance(self.bottle_configs.get(key), dict):
            self.bottle_configs[key]["threshold"] = int(threshold)
        else:
            self.bottle_configs[key] = int(threshold)
        self.data_manager.save_bottle_configs(self.bottle_configs)
        
        self.combo_size.setCurrentText(size)
        self.combo_flavour.setCurrentText(flavour)
        
        self.stacked_widget.setCurrentIndex(0)
        self.transition_to_state("HOME")

    @pyqtSlot()
    def on_setup_cancel_clicked(self):
        self.stacked_widget.setCurrentIndex(0)
        self.transition_to_state("HOME")

    @pyqtSlot()
    def on_start_clicked(self):
        self.transition_to_state("RUNNING")

    @pyqtSlot()
    def on_end_clicked(self):
        # Stop background pipelines
        self.watchdog_timer.stop()
        self.camera_thread.stop_run()
        self.vision_thread.stop_pipeline()

        # Compile session database entry
        duration = time.time() - self.session_start_time
        total_cnt = self.current_metrics.get("total", 0)
        accepted_cnt = self.current_metrics.get("accepted", 0)
        rejected_cnt = self.current_metrics.get("rejected", 0)
        
        session_record = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.session_start_time)),
            "end_timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time())),
            "bottle_size": self.combo_size.currentText(),
            "flavour": self.combo_flavour.currentText(),
            "threshold": self.current_threshold(),
            "total": total_cnt,
            "accepted": accepted_cnt,
            "rejected": rejected_cnt,
            "yield": self.current_metrics.get("yield", 100.0),
            "duration_sec": int(duration)
        }

        self.history.insert(0, session_record)
        self.data_manager.save_history(self.history)

        self.transition_to_state("HOME")

    def get_threshold_for(self, key):
        config = self.bottle_configs.get(key, 300)
        if isinstance(config, dict):
            return int(config.get("threshold", 300))
        return int(config)

    def get_exposure_for(self, key):
        config = self.bottle_configs.get(key)
        if isinstance(config, dict):
            return int(config.get("exposure_us", 3000))
        return 3000

    def current_threshold(self):
        key = f"{self.combo_size.currentText()}_{self.combo_flavour.currentText()}"
        return self.get_threshold_for(key)

    # --- Thread Signals & Slots ---
    @pyqtSlot(np.ndarray, dict, bool, str)
    def on_frame_processed(self, frame, metrics, is_pass, reason):
        if self.ui_state != "RUNNING":
            return
        self.update_image_display(frame)
        self.update_run_metrics(metrics)
        
        if is_pass:
            self.status_comment.setText("Status: PASS")
            self.status_comment.setStyleSheet("color: #27ae60; font-weight: bold; font-size: 16px;")
        else:
            self.status_comment.setText(f"REJECTED: {reason}")
            self.status_comment.setStyleSheet("color: #c0392b; font-weight: bold; font-size: 16px;")

    @pyqtSlot(dict)
    def on_metrics_ready(self, metrics):
        if self.ui_state != "RUNNING":
            return
        self.update_run_metrics(metrics)

    @pyqtSlot()
    def on_trigger_received(self):
        if self.ui_state != "RUNNING":
            return
        self.last_trigger_time = time.time()

    def check_end_button_status(self):
        if self.ui_state != "RUNNING":
            return
        
        elapsed = time.time() - self.last_trigger_time
        if elapsed > 5.0:
            self.btn_end.setEnabled(True)
            self.btn_end.setText("End Run")
            self.btn_end.setStyleSheet("background-color: #c0392b; color: white;")
        else:
            self.btn_end.setEnabled(False)
            countdown = max(0, int(5.0 - elapsed))
            self.btn_end.setText(f"End Run (Locked {countdown}s)")
            self.btn_end.setStyleSheet("background-color: #552222; color: #888888;")

    def check_start_button_status(self):
        if self.ui_state == "HOME":
            if self.stm32_ready and self.camera_ready:
                self.btn_start.setEnabled(True)
                self.btn_start.setText("Start Run")
                self.btn_start.setStyleSheet("background-color: #27ae60; color: white;")
            else:
                self.btn_start.setEnabled(False)
                self.btn_start.setText("Start Run (Hardware Not Ready)")
                self.btn_start.setStyleSheet("background-color: #552222; color: #888888;")

    @pyqtSlot(str)
    def update_serial_status(self, status):
        self.lbl_serial.setText(f"STM32: {status}")
        self.stm32_ready = "Connected" in status
        self.check_start_button_status()

    @pyqtSlot(str)
    def update_camera_status(self, status):
        self.lbl_camera.setText(status)
        self.camera_ready = "Connected" in status or "Ready" in status
        self.check_start_button_status()

    @pyqtSlot(int)
    def on_hardware_button_pressed(self, btn_id):
        """
        D-pad navigation - clean Tab-based model:
          Up   (1) = Shift+Tab  (prev widget)
          Down (2) = Tab        (next widget)
          Left (3) = cycle backward / decrease value
          Right(4) = cycle forward  / increase value
          OK   (5) = click focused button
          S    (6) = Start or End run directly
        """
        focused = QApplication.focusWidget()
        if not focused:
            return

        # ── Button 6: Start / End shortcut ───────────────────────────────────
        if btn_id == 6:
            if self.ui_state == "HOME" and self.btn_start.isEnabled():
                self.btn_start.click()
            elif self.ui_state == "RUNNING" and self.btn_end.isEnabled():
                self.btn_end.click()
            return

        # ── Up / Down  →  Shift+Tab / Tab (Move between widgets) ────────────
        if btn_id == 1:   # Up → Reverse Tab
            focused.focusPreviousChild()
            return
        if btn_id == 2:   # Down → Tab
            focused.focusNextChild()
            return

        # ── Left / Right  →  cycle values ────────────────────────────────────
        if btn_id in (3, 4):
            forward = (btn_id == 4)

            if isinstance(focused, QComboBox):
                n = focused.count()
                if n:
                    focused.setCurrentIndex((focused.currentIndex() + (1 if forward else -1)) % n)
                return

            if isinstance(focused, QSlider):
                focused.setValue(focused.value() + (1 if forward else -1))
                return

            # History list: Left/Right scroll records
            if isinstance(focused, QListWidget) and focused.count():
                row = focused.currentRow()
                if forward:
                    focused.setCurrentRow(min(row + 1, focused.count() - 1))
                else:
                    focused.setCurrentRow(max(row - 1, 0))
                return

            # Anything else: treat as Tab navigation
            if forward:
                focused.focusNextChild()
            else:
                focused.focusPreviousChild()
            return

        # ── OK (5)  →  click button / confirm ────────────────────────────────
        if btn_id == 5:
            if isinstance(focused, QPushButton):
                focused.click()
            # ComboBox and Slider already changed via L/R — no popup needed
            return



    # --- Screen Switching & Navigation ---
    def go_to_history_screen(self):
        self.history_list.clear()
        self.history = self.data_manager.load_history()
        for idx, entry in enumerate(self.history):
            item_txt = f"{entry.get('timestamp')} - {entry.get('bottle_size')} {entry.get('flavour')}"
            list_item = QListWidgetItem(item_txt)
            list_item.setData(Qt.ItemDataRole.UserRole, idx)
            self.history_list.addItem(list_item)
        self.stacked_widget.setCurrentIndex(1)
        if self.history_list.count() > 0:
            self.history_list.setCurrentRow(0)
            self.history_list.setFocus()
        else:
            self.btn_hist_back.setFocus()

    def go_to_home_screen(self):
        self.stacked_widget.setCurrentIndex(0)
        self.transition_to_state("HOME")
        self.btn_setup.setFocus()

    def on_history_selection_changed(self):
        selected_items = self.history_list.selectedItems()
        if not selected_items:
            return
        idx = selected_items[0].data(Qt.ItemDataRole.UserRole)
        entry = self.history[idx]
        
        self.lbl_hist_time.setText(entry.get("timestamp"))
        self.lbl_hist_endtime.setText(entry.get("end_timestamp", "-"))
        self.lbl_hist_size.setText(entry.get("bottle_size"))
        self.lbl_hist_flavour.setText(entry.get("flavour"))
        self.lbl_hist_total.setText(str(entry.get("total")))
        self.lbl_hist_accepted.setText(str(entry.get("accepted", "-")))
        self.lbl_hist_rejected.setText(str(entry.get("rejected")))
        self.lbl_hist_yield.setText(f"{entry.get('yield', 100.0):.1f} %")
        
        duration = entry.get("duration_sec", 0)
        minutes, seconds = divmod(duration, 60)
        self.lbl_hist_duration.setText(f"{minutes}m {seconds}s")
        
        self.lbl_hist_threshold.setText(f"{entry.get('threshold')} px")

    # --- UI Helper Operations ---
    def update_image_display(self, bgr_frame):
        # Keep a reference to the numpy array so PyQt's QImage doesn't lose the pointer!
        self._current_rgb_image = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        h, w, ch = self._current_rgb_image.shape
        bytes_per_line = ch * w
        qt_img = QImage(self._current_rgb_image.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
        
        scaled_pixmap = QPixmap.fromImage(qt_img).scaled(
            512, 320, 
            Qt.AspectRatioMode.KeepAspectRatio, 
            Qt.TransformationMode.SmoothTransformation
        )
        self.left_display.setPixmap(scaled_pixmap)

    def update_run_metrics(self, metrics):
        self.current_metrics = metrics
        self.lbl_run_total.setText(str(metrics.get("total", 0)))
        self.lbl_run_accepted.setText(str(metrics.get("accepted", 0)))
        self.lbl_run_rejected.setText(str(metrics.get("rejected", 0)))
        self.lbl_run_yield.setText(f"{metrics.get('yield', 100.0):.1f} %")

    def closeEvent(self, event):
        self.camera_thread.shutdown()
        self.vision_thread.stop()
        self.serial_thread.stop()
        event.accept()

class KioskFilter(QObject):
    """Blocks all native physical keyboard inputs to simulate a true hardware kiosk."""
    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            return True
        return super().eventFilter(obj, event)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Install the Kiosk Filter to globally block physical keyboard keys
    kiosk_filter = KioskFilter()
    app.installEventFilter(kiosk_filter)
    
    window = OperatorDashboard()
    window.show()
    sys.exit(app.exec())
