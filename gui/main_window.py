import os
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QSplitter, QMenuBar, QMenu, QToolBar, QStatusBar,
    QMessageBox, QFileDialog, QListWidget, QLabel,
    QTabWidget, QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator, QProgressBar,
    QTextBrowser, QDialog, QDialogButtonBox, QListWidgetItem, QAbstractItemView, QGraphicsView, QStyle, QApplication, QScrollArea, QFrame
)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QAction, QIcon, QFont, QShortcut, QKeySequence
from gui.page_viewer import PageViewer
from gui.edit_history import ActionHistory
from gui.edit_actions import UpdateRoleAction
from gui.edit_persistence import PersistenceManager
from gui.toast import ToastNotification


class RoleSelectionDialog(QDialog):
    """
    Acrobat Reading Order-style role selection dialog.

    Roles are organised into categories.  Unsupported / planned roles are
    shown but disabled (greyed out) so the user understands what is coming
    without being able to accidentally select an unimplemented structural type.

    Genuinely implemented (end-to-end) roles that can be selected:
        P    — Text/Paragraph
        H1   — Heading 1
        H2   — Heading 2
        H3   — Heading 3
        H4   — Heading 4  (pass-through; no special renderer needed)
        H5   — Heading 5  (pass-through)
        H6   — Heading 6  (pass-through)

    Disabled / planned (visible but not selectable):
        Figure           — requires image-region association (Phase 21)
        Table            — requires TR/TD structure (Phase 21)
        Form Field       — requires annotation association (Phase 21)
        Formula          — planned
        Note             — planned
        Reference        — planned
        Background/Artifact — planned
    """

    # (label, role_string, enabled, shortcut_hint)
    ROLES = [
        # --- Text ---
        ("TEXT", None, False, ""),
        ("P  \u2014 Text / Paragraph", "P", True, "P"),
        # --- Headings ---
        ("HEADINGS", None, False, ""),
        ("H1  \u2014 Heading 1", "H1", True, "H → 1"),
        ("H2  \u2014 Heading 2", "H2", True, "H → 2"),
        ("H3  \u2014 Heading 3", "H3", True, "H → 3"),
        ("H4  \u2014 Heading 4", "H4", True, "H → 4"),
        ("H5  \u2014 Heading 5", "H5", True, "H → 5"),
        ("H6  \u2014 Heading 6", "H6", True, "H → 6"),
        # --- Figure (disabled) ---
        ("FIGURE", None, False, ""),
        ("Figure  [planned Phase 21]", None, False, "F"),
        ("Figure / Caption  [planned]", None, False, ""),
        # --- Form (disabled) ---
        ("FORM", None, False, ""),
        ("Form Field  [planned Phase 21]", None, False, ""),
        # --- Table (disabled) ---
        ("TABLE", None, False, ""),
        ("Table  [planned Phase 21]", None, False, "T"),
        ("Cell  [planned Phase 21]", None, False, "C"),
        # --- Other (disabled) ---
        ("OTHER", None, False, ""),
        ("Formula  [planned]", None, False, "M"),
        ("Note  [planned]", None, False, "N"),
        ("Reference  [planned]", None, False, "R"),
        ("Background / Artifact  [planned]", None, False, "B"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Tag Role")
        self.setMinimumWidth(420)
        self.selected_role = None

        layout = QVBoxLayout(self)

        hint = QLabel(
            "Choose a structural role for the selected content.\n"
            "Greyed-out roles are planned for a future phase."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #aaaaaa; font-size: 11px; margin-bottom: 6px;")
        layout.addWidget(hint)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_widget.setAlternatingRowColors(False)
        self.list_widget.setStyleSheet(
            "QListWidget { background: #252526; color: #dcdcdc; border: 1px solid #444; "
            "font-family: Consolas, monospace; font-size: 13px; }"
            "QListWidget::item:selected { background: #0e639c; color: white; }"
            "QListWidget::item:disabled { color: #555; }"
        )

        category_font = QFont()
        category_font.setBold(True)
        category_font.setPointSize(9)

        for label, role, enabled, shortcut in self.ROLES:
            if role is None and enabled is False and shortcut == "":
                # Category header
                item = QListWidgetItem(f"  {label}")
                item.setFont(category_font)
                item.setForeground(Qt.darkGray)
                item.setFlags(Qt.NoItemFlags)  # not selectable
                item.setData(Qt.UserRole, None)
            else:
                sep = "  — "
                display = f"  {label}"
                if shortcut:
                    if sep in label:
                        role_code = label.split(sep)[0].strip()
                        desc = label.split(sep, 1)[-1]
                        display = f"  {role_code:<6}  {shortcut:<8}  {desc}"
                    else:
                        display = f"  [{shortcut}]  {label.strip()}"
                item = QListWidgetItem(display)
                item.setData(Qt.UserRole, role)
                if not enabled or role is None:
                    item.setFlags(Qt.NoItemFlags)
                    item.setForeground(Qt.darkGray)

            self.list_widget.addItem(item)

        self.list_widget.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self.list_widget)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self):
        items = self.list_widget.selectedItems()
        if items:
            role = items[0].data(Qt.UserRole)
            if role:
                self.selected_role = role
                self.accept()
                return
        QMessageBox.warning(self, "No Selection", "Please select an enabled role.")

    def _on_double_click(self, item):
        role = item.data(Qt.UserRole)
        if role:
            self.selected_role = role
            self.accept()


def force_quit_application():
    """Quit TagPilot now. QApplication.quit() (Qt 6) first asks every window to
    close and is cancelled by any window that refuses (e.g. a blocking dialog),
    so: stop background work, then exit the event loops directly. A short safety
    timer ends the process if a background thread would keep it alive."""
    import threading
    app = QApplication.instance()
    for w in (app.topLevelWidgets() if app else []):
        if isinstance(w, MainWindow):
            w._force_quit = True
            try:
                w.shutdown_background_work(wait_ms=3000)
            except Exception:
                pass
    t = threading.Timer(4.0, lambda: os._exit(0))
    t.daemon = True
    t.start()
    if app:
        app.exit(0)


class InternetBlockDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("No Internet Connection")
        self.setFixedSize(450, 220)
        # owned by the TagPilot window: stays above TagPilot only, never above other apps
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setWindowModality(Qt.WindowModal if parent is not None else Qt.ApplicationModal)
        self.setStyleSheet("QDialog { background-color: #252526; border: 2px solid #ff5555; border-radius: 8px; }")
        
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(15)
        layout.setContentsMargins(30, 30, 30, 30)
        
        lbl = QLabel("⚠️ No Internet Connection")
        lbl.setStyleSheet("color: #ff5555; font-size: 18px; font-weight: bold;")
        lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(lbl)
        
        desc = QLabel("TagPilot requires an active internet connection to function.\nWe could not reach the server.\n\nPlease connect to the internet and click Retry.")
        desc.setStyleSheet("color: #cccccc; font-size: 13px;")
        desc.setAlignment(Qt.AlignCenter)
        desc.setWordWrap(True)
        layout.addWidget(desc)
        
        from PySide6.QtWidgets import QPushButton, QApplication, QHBoxLayout
        btn_layout = QHBoxLayout()
        
        self.quit_btn = QPushButton("Quit Application")
        self.quit_btn.setMinimumHeight(35)
        self.quit_btn.setStyleSheet("QPushButton { background-color: #3d3d3d; color: white; border: none; border-radius: 4px; } QPushButton:hover { background-color: #4d4d4d; }")
        self.quit_btn.clicked.connect(self.quit_app)
        btn_layout.addWidget(self.quit_btn)

        self.retry_btn = QPushButton("Retry Connection")
        self.retry_btn.setMinimumHeight(35)
        self.retry_btn.setStyleSheet("QPushButton { background-color: #0078d7; color: white; border: none; border-radius: 4px; font-weight: bold; } QPushButton:hover { background-color: #005a9e; }")
        self.retry_btn.clicked.connect(self.manual_retry)
        btn_layout.addWidget(self.retry_btn)
        
        layout.addLayout(btn_layout)
        
        from PySide6.QtCore import QTimer
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.auto_retry)
        self.timer.start(5000)
        
    def showEvent(self, event):
        super().showEvent(event)
        if not self.timer.isActive():
            self.timer.start(5000)
        if self.parent():
            # Center on parent window
            parent_rect = self.parent().geometry()
            self.move(parent_rect.center() - self.rect().center())
        
    def auto_retry(self):
        # Checked in a thread so the dialog never freezes on a network timeout
        if getattr(self, "worker", None) is not None and self.worker.isRunning():
            return
        from PySide6.QtCore import QThread, Signal

        class CheckWorker(QThread):
            res = Signal(bool)

            def run(self):
                from app.activation import is_online
                self.res.emit(is_online())

        self.worker = CheckWorker(self)
        self.worker.res.connect(self._on_auto_res)
        self.worker.start()

    def _on_auto_res(self, connected):
        self.retry_btn.setText("Retry Connection")
        self.retry_btn.setEnabled(True)
        if connected:
            self.timer.stop()
            self.accept()

    def manual_retry(self):
        self.retry_btn.setText("Checking...")
        self.retry_btn.setEnabled(False)
        self.auto_retry()

    def reject(self):
        # Esc cannot dismiss the block: only Retry (when online) or Quit
        return

    def done(self, result):
        from app.activation import park_thread
        self.timer.stop()
        park_thread(getattr(self, "worker", None))
        super().done(result)

    def closeEvent(self, event):
        # Alt+F4 / closing the window = Quit Application
        event.accept()
        self.quit_app()

    def quit_app(self):
        self.timer.stop()
        self.quit_btn.setEnabled(False)
        self.retry_btn.setEnabled(False)
        force_quit_application()



class MainWindow(QMainWindow):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.setWindowTitle("TagPilot")
        import os
        from PySide6.QtGui import QIcon
        icon_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "icon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.resize(1300, 850)
        self.action_history = ActionHistory()
        self.all_elements = []
        self.live_pdf_worker = None
        self.worker = None

        self.init_ui()
        self.update_recent_projects_menu()
        
        self.internet_blocker = None
        self._conn_worker = None
        self._license_worker = None
        self._license_dialog_open = False
        self._last_license_verify = 0.0
        self._pending_struct_deletes = []
        self._force_quit = False

    # ------------------------------------------------------------------
    # Runtime guards: internet + licence (started after the start-up gate)
    # ------------------------------------------------------------------
    def start_runtime_guards(self):
        import time
        from PySide6.QtCore import QTimer
        self._last_license_verify = time.time()   # the start-up gate just verified it
        self.connection_timer = QTimer(self)
        self.connection_timer.timeout.connect(self._check_internet_bg)
        self.connection_timer.start(10000)
        self.license_timer = QTimer(self)
        self.license_timer.timeout.connect(self._check_license_bg)
        self.license_timer.start(60000)

    def _check_internet_bg(self):
        if self.internet_blocker and self.internet_blocker.isVisible():
            return
        if self._conn_worker is not None and self._conn_worker.isRunning():
            return
        from PySide6.QtCore import QThread, Signal

        class CheckWorker(QThread):
            result = Signal(bool)

            def run(self):
                from app.activation import is_online
                self.result.emit(is_online())

        self._conn_worker = CheckWorker(self)
        self._conn_worker.result.connect(self._on_internet_checked)
        self._conn_worker.start()

    def _on_internet_checked(self, is_connected):
        if not is_connected:
            if not self.internet_blocker:
                self.internet_blocker = InternetBlockDialog(self)
            if not self.internet_blocker.isVisible():
                self.log("[WARN] Internet connection lost - TagPilot is blocked until it is back.")
                self.internet_blocker.exec()

    def _check_license_bg(self):
        """Every minute: local expiry check; every 30 min: server verification."""
        import time
        from app.activation import LicenseManager, LICENSE_REVERIFY_SECONDS, format_expiry
        if self._license_dialog_open:
            return
        lm = LicenseManager()
        if not lm.get_saved_code():
            self._require_activation("Please activate TagPilot to continue.")
            return
        if lm.is_expired():
            exp = format_expiry(lm.get_expiry())
            lm.clear_license()
            self._require_activation(f"Your activation code expired on {exp}. Please enter a new code.")
            return
        if time.time() - self._last_license_verify < LICENSE_REVERIFY_SECONDS:
            return
        if self._license_worker is not None and self._license_worker.isRunning():
            return
        from PySide6.QtCore import QThread, Signal

        class VerifyWorker(QThread):
            result = Signal(str, str)

            def run(self):
                lm2 = LicenseManager()
                code = lm2.get_saved_code()
                if not code:
                    self.result.emit("invalid", "Please activate TagPilot to continue.")
                    return
                status, msg = lm2.verify_online(code)
                self.result.emit(status, msg)

        self._license_worker = VerifyWorker(self)
        self._license_worker.result.connect(self._on_license_verified)
        self._license_worker.start()

    def _on_license_verified(self, status, message):
        import time
        from app.activation import LicenseManager
        if status == LicenseManager.VALID:
            self._last_license_verify = time.time()
        elif status == LicenseManager.INVALID:
            LicenseManager().clear_license()
            self._require_activation(message or "Your activation code is no longer valid.")
        # UNREACHABLE: keep working on the stored licence; retried next minute

    def _require_activation(self, reason):
        """Expired / revoked licence while the tool is open: block until a new code
        is activated, otherwise close TagPilot."""
        if self._license_dialog_open:
            return
        self._license_dialog_open = True
        try:
            if self.worker and self.worker.isRunning():
                self.worker.stop()
            from app.activation import ActivationDialog
            dlg = ActivationDialog(self, reason)
            if dlg.exec() != QDialog.Accepted:
                force_quit_application()
                return
            import time
            self._last_license_verify = time.time()
        finally:
            self._license_dialog_open = False

    def init_ui(self):
        # 1. Menus
        self.init_menus()
        
        # 2. Toolbar
        self.init_toolbar()
        
        # 3. Central Widget and Splitters
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(8, 8, 8, 8)
        
        # Vertical splitter for main content vs bottom panels
        v_splitter = QSplitter(Qt.Orientation.Vertical)
        
        # Horizontal splitter for Left, Center, Right panels
        h_splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # ---------------------------------------------------------
        # LEFT PANEL (QTabWidget)
        # ---------------------------------------------------------
        self.left_tabs = QTabWidget()
        
        # Project Explorer
        self.project_explorer = QTreeWidget(self)
        self.project_explorer.hide()
        self.project_explorer.setHeaderHidden(True)
        self.project_explorer.setIndentation(15)
        root = QTreeWidgetItem(["Workspace: No project open"])
        self.project_explorer.addTopLevelItem(root)
        # self.left_tabs.addTab(self.project_explorer, "Project Explorer")

        # Elements Panel
        elements_widget = QWidget(self)
        elements_widget.hide()
        elements_layout = QVBoxLayout(elements_widget)
        elements_layout.setContentsMargins(0,0,0,0)
        
        from PySide6.QtWidgets import QComboBox
        self.pipeline_combo = QComboBox()
        self.pipeline_combo.addItems([
            "1. Raw JSON", "2. Normalized", "3. Geometry",
            "4. Visual", "5. Detection", "6. Analysis",
            "7. Consistency", "8. QA", "9. Rules"
        ])
        self.pipeline_combo.currentIndexChanged.connect(self.on_pipeline_view_changed)
        elements_layout.addWidget(self.pipeline_combo)
        
        self.elements_tree = QTreeWidget()
        self.elements_tree.setHeaderLabels(["Type", "ID", "Page"])
        self.elements_tree.setColumnWidth(0, 100)
        self.elements_tree.setColumnWidth(1, 40)
        self.elements_tree.itemSelectionChanged.connect(self.on_element_selected)
        self.elements_tree.itemExpanded.connect(self._on_elements_tree_expanded)
        self.elements_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.elements_tree.customContextMenuRequested.connect(self.on_elements_tree_context_menu)

        elements_layout.addWidget(self.elements_tree)
        # self.left_tabs.addTab(elements_widget, "Elements")
        
        # Tags Panel
        self.tags_tree = QTreeWidget()
        self.tags_tree.setHeaderHidden(True)
        self.tags_tree.setIndentation(15)
        self.tags_tree.itemSelectionChanged.connect(self.on_tag_tree_selected)
        self.tags_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tags_tree.customContextMenuRequested.connect(self.on_tag_tree_context_menu)
        self.tags_tree.itemChanged.connect(self.on_tag_text_changed)
        self.tags_tree.installEventFilter(self)   # Delete key -> Delete Tag
        
        self.left_tabs.addTab(self.tags_tree, "Tags")
        
        # Show left panel by default, and set Tags as active
        self.left_tabs.setCurrentWidget(self.tags_tree)
        self.left_tabs.show() # show by default
        
        h_splitter.addWidget(self.left_tabs)
        
        # ---------------------------------------------------------
        # CENTER PANEL (QTabWidget)
        # ---------------------------------------------------------
        self.center_tabs = QTabWidget()
        
        # Page Viewer
        self.page_viewer = PageViewer()
        self.page_viewer._main_window = self
        self.page_viewer.node_selected_signal.connect(self.on_canvas_node_selected)
        self.page_viewer.node_right_clicked_signal.connect(self.on_canvas_node_right_clicked)
        self.page_viewer.tag_region_selected_signal.connect(self.on_tag_region_selected)
        self.page_viewer.bbox_modified_signal.connect(self.on_canvas_bbox_modified)

        self.center_tabs.addTab(self.page_viewer, "Current Page Preview")
        
        h_splitter.addWidget(self.center_tabs)
        
        # ---------------------------------------------------------
        # RIGHT PANEL (QTabWidget)
        # ---------------------------------------------------------
        self.right_tabs = QTabWidget()
        
        # Properties
        self.property_panel = QTextBrowser()
        self.property_panel.setText("No Element Selected")
        self.property_panel.setStyleSheet("background-color: #1e1e1e; border: none; padding: 10px;")
        self.right_tabs.addTab(self.property_panel, "Properties")
        
        # History
        self.history_panel = QListWidget(self)
        self.history_panel.hide()
        self.history_panel.setStyleSheet("background-color: #1e1e1e; border: none; padding: 5px;")
        # self.right_tabs.addTab(self.history_panel, "History")
        
        h_splitter.addWidget(self.right_tabs)
        
        # Set Splitter Sizes
        h_splitter.setSizes([350, 1400, 0])
        h_splitter.setChildrenCollapsible(True)
        h_splitter.setHandleWidth(12)
        v_splitter.addWidget(h_splitter)
        
        # ---------------------------------------------------------
        # BOTTOM PANEL (QTabWidget)
        # ---------------------------------------------------------
        self.bottom_tabs = QTabWidget()
        
        # Console
        self.console = QTextBrowser(self)
        self.console.hide()
        self.console.setStyleSheet("background-color: #1e1e1e; color: #d4d4d4;")
        self.console.append("Application initialized.")
        # self.bottom_tabs.addTab(self.console, "Console")
        
        # Progress (Widget holding a progress bar and label)
        progress_widget = QWidget()
        p_layout = QVBoxLayout(progress_widget)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("")
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #444;
                border-radius: 4px;
                text-align: center;
                background-color: #252526;
                color: white;
                height: 18px;
            }
            QProgressBar::chunk {
                background-color: #0e639c;
                border-radius: 2px;
            }
        """)
        self.progress_label = QLabel("Current Task Progress:")
        self.timer_label = QLabel("Run time: 00:00")
        
        header_layout = QHBoxLayout()
        header_layout.addWidget(self.progress_label)
        header_layout.addStretch()
        header_layout.addWidget(self.timer_label)
        
        p_layout.addLayout(header_layout)
        p_layout.addWidget(self.progress_bar)
        p_layout.addStretch()
        self.bottom_tabs.addTab(progress_widget, "Progress")
        

        v_splitter.addWidget(self.bottom_tabs)
        
        # Set Vertical Splitter Sizes (Top panels: 850, Bottom panels: 0)
        v_splitter.setSizes([850, 0])
        v_splitter.setChildrenCollapsible(True)
        v_splitter.setHandleWidth(12)
        main_layout.addWidget(v_splitter)
        
        # Add buttons to splitter handles
        self.setup_splitter_button(h_splitter, 1, is_vertical=False, is_left=True, index_to_toggle=0)
        self.setup_splitter_button(h_splitter, 2, is_vertical=False, is_left=False, index_to_toggle=2)
        self.setup_splitter_button(v_splitter, 1, is_vertical=True, is_left=False, index_to_toggle=1)
        
        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self.statusBar.showMessage("Ready")

        # Global delete shortcuts
        self.shortcut_delete = QShortcut(QKeySequence(Qt.Key_Delete), self)
        self.shortcut_delete.activated.connect(lambda: self.delete_selected_element(from_backspace=False))
        self.shortcut_backspace = QShortcut(QKeySequence(Qt.Key_Backspace), self)
        self.shortcut_backspace.activated.connect(lambda: self.delete_selected_element(from_backspace=True))

        # Pipeline run time timer
        from PySide6.QtCore import QTimer
        self.pipeline_timer = QTimer(self)
        self.pipeline_timer.timeout.connect(self.update_pipeline_timer)
        self.pipeline_seconds = 0

    def _app_icon(self):
        """TagPilot icon (works from source and from the installed exe)."""
        import sys
        from PySide6.QtGui import QIcon
        icon = QApplication.windowIcon()
        if icon.isNull():
            icon = self.windowIcon()
        if icon.isNull():
            base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            for name in ("icon.ico", "icon.png"):
                p = os.path.join(base, name)
                if os.path.exists(p):
                    icon = QIcon(p)
                    break
        if icon.isNull():
            icon = self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        return icon

    def shutdown_background_work(self, wait_ms=3000):
        """Stop timers and background threads (used by Quit / exit)."""
        for t in ("connection_timer", "license_timer", "pipeline_timer"):
            if hasattr(self, t):
                try:
                    getattr(self, t).stop()
                except Exception:
                    pass
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(wait_ms)
        if self.live_pdf_worker:
            try:
                self.live_pdf_worker.stop()
            except Exception:
                pass
        if hasattr(self, "tray_icon"):
            try:
                self.tray_icon.hide()
            except Exception:
                pass

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if obj is getattr(self, "tags_tree", None) and event.type() == QEvent.KeyPress \
                and event.key() == Qt.Key_Delete and not self.tags_tree.state() == QAbstractItemView.EditingState:
            if self.tags_tree.selectedItems():
                self.delete_selected_tags()
            return True
        return super().eventFilter(obj, event)

    def update_pipeline_timer(self):
        self.pipeline_seconds += 1
        mins = self.pipeline_seconds // 60
        secs = self.pipeline_seconds % 60
        self.timer_label.setText(f"Run time: {mins:02d}:{secs:02d}")

    def setup_splitter_button(self, splitter, handle_index, is_vertical, is_left, index_to_toggle):
        from PySide6.QtWidgets import QPushButton, QVBoxLayout, QHBoxLayout
        handle = splitter.handle(handle_index)
        layout = QVBoxLayout(handle) if not is_vertical else QHBoxLayout(handle)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        btn = QPushButton()
        btn.setCursor(Qt.PointingHandCursor)
        if not is_vertical:
            btn.setFixedSize(12, 40)
            layout.setAlignment(Qt.AlignVCenter)
        else:
            btn.setFixedSize(40, 12)
            layout.setAlignment(Qt.AlignHCenter)
            
        btn.setStyleSheet("QPushButton { border: none; background-color: transparent; color: #888; font-size: 10px; }"
                          "QPushButton:hover { background-color: #555; color: white; }")
        
        layout.addWidget(btn)
        btn.setProperty("restore_size", 350 if not is_vertical else 200)
        
        def toggle_panel():
            sizes = splitter.sizes()
            if sizes[index_to_toggle] == 0:
                sizes[index_to_toggle] = btn.property("restore_size") or 300
            else:
                btn.setProperty("restore_size", sizes[index_to_toggle])
                sizes[index_to_toggle] = 0
            splitter.setSizes(sizes)
            update_arrow()

        btn.clicked.connect(toggle_panel)

        def update_arrow():
            sizes = splitter.sizes()
            is_collapsed = (sizes[index_to_toggle] == 0)
            if not is_vertical:
                if is_left:
                    btn.setText("▶" if is_collapsed else "◀")
                else:
                    btn.setText("◀" if is_collapsed else "▶")
            else:
                btn.setText("▲" if is_collapsed else "▼")
                
        update_arrow()
        splitter.splitterMoved.connect(lambda pos, index: update_arrow())

    def init_menus(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")
        
        new_proj_action = QAction("New Project", self)
        new_proj_action.triggered.connect(self.on_new_project)
        file_menu.addAction(new_proj_action)
        
        open_proj_action = QAction("Open Project", self)
        open_proj_action.triggered.connect(self.on_open_project)
        file_menu.addAction(open_proj_action)
        
        self.recent_menu = QMenu("Recent Projects", self)
        file_menu.addMenu(self.recent_menu)
        
        file_menu.addSeparator()
        logs_action = QAction("Open Log Folder", self)
        logs_action.triggered.connect(self.on_open_log_folder)
        file_menu.addAction(logs_action)

        file_menu.addSeparator()
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

    def on_open_log_folder(self):
        from app.logging_setup import get_log_dir, get_log_file
        d = get_log_dir()
        if not d or not os.path.isdir(d):
            QMessageBox.information(self, "Logs", "No log folder is available.")
            return
        try:
            if hasattr(os, "startfile"):
                os.startfile(d)
            else:
                from PySide6.QtGui import QDesktopServices
                from PySide6.QtCore import QUrl
                QDesktopServices.openUrl(QUrl.fromLocalFile(d))
        except Exception:
            QMessageBox.information(self, "Logs", f"Log files are in:\n{d}\n\nCurrent log:\n{get_log_file() or '-'}")

    def init_toolbar(self):
        toolbar = QToolBar("Main Toolbar", self)
        toolbar.setIconSize(QSize(24, 24))
        toolbar.setMovable(False)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        scroll.setMinimumHeight(65)
        
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(8)
        
        def add_action(action):
            from PySide6.QtWidgets import QToolButton
            btn = QToolButton()
            btn.setDefaultAction(action)
            btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            layout.addWidget(btn)
            
        def add_separator():
            pass
            # from PySide6.QtWidgets import QFrame
            # line = QFrame()
            # line.setFrameShape(QFrame.VLine)
            # line.setFrameShadow(QFrame.Sunken)
            # layout.addWidget(line)
            
        self.undo_action = QAction(QIcon.fromTheme("edit-undo"), "Undo", self)
        self.undo_action.setShortcut("Ctrl+Z")
        self.undo_action.triggered.connect(self.undo)
        self.addAction(self.undo_action)
        
        self.redo_action = QAction(QIcon.fromTheme("edit-redo"), "Redo", self)
        self.redo_action.setShortcut("Ctrl+Y")
        self.redo_action.triggered.connect(self.redo)
        self.addAction(self.redo_action)
        self._update_undo_redo_actions()
        
        new_proj_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon), "New Project", self)
        new_proj_action.triggered.connect(self.on_new_project)
        add_action(new_proj_action)
        
        open_proj_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon), "Open Project", self)
        open_proj_action.triggered.connect(self.on_open_project)
        add_action(open_proj_action)
        
        self.save_proj_action_tb = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton), "Save Project", self)
        self.save_proj_action_tb.triggered.connect(self.on_save_project)
        # self.save_proj_action_tb.setEnabled(False)
        # add_action(self.save_proj_action_tb)
        
        add_separator()
        
        self.tag_content_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_CommandLink), "Tag Content", self)
        self.tag_content_action.setToolTip("Draw a rectangle over untagged native text to tag it.")
        self.tag_content_action.setCheckable(True)
        self.tag_content_action.triggered.connect(self.on_tag_content_toggled)
        self.tag_content_action.setEnabled(False)
        # add_action(self.tag_content_action)

        add_separator()
        
        from PySide6.QtWidgets import QComboBox, QLabel
        self.doc_type_label = QLabel(" PDF Type: ")
        layout.addWidget(self.doc_type_label)
        self.doc_type_combo = QComboBox()
        self.doc_type_combo.addItems(["Scanned PDF", "Digital PDF"])
        self.doc_type_combo.setEnabled(False)
        self.doc_type_combo.currentTextChanged.connect(self.on_doc_type_changed)
        layout.addWidget(self.doc_type_combo)

        self.generate_json_action = QAction("Run Pipeline", self)
        self.generate_json_action.setToolTip("Runs the engine locally if raw JSON exists, otherwise extracts the pages first.")
        self.generate_json_action.triggered.connect(self.on_generate_json)
        self.generate_json_action.setEnabled(False)
        add_action(self.generate_json_action)
        
        self.stop_pipeline_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserStop), "Stop Pipeline", self)
        self.stop_pipeline_action.setToolTip("Stops the currently running pipeline safely and caches the progress.")
        self.stop_pipeline_action.triggered.connect(self.on_stop_pipeline)
        self.stop_pipeline_action.setEnabled(False)
        add_action(self.stop_pipeline_action)
        
        self.upload_json_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowUp), "Upload JSON", self)
        self.upload_json_action.setToolTip("Upload a raw_semantic.json file manually.")
        self.upload_json_action.triggered.connect(self.on_upload_json)
        self.upload_json_action.setEnabled(False)
        # add_action(self.upload_json_action)
        
        pass
        
        self.download_pdf_action = QAction("Download PDF", self)
        self.download_pdf_action.setToolTip("Download the generated Tagged PDF.")
        self.download_pdf_action.triggered.connect(self.on_download_pdf)
        self.download_pdf_action.setEnabled(False)
        add_action(self.download_pdf_action)
        

        
        add_separator()
        
        self.doc_props_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView), "Document Properties", self)
        self.doc_props_action.triggered.connect(self.on_document_properties)
        self.doc_props_action.setEnabled(False)
        add_action(self.doc_props_action)
        
        add_separator()
        
        self.delete_proj_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon), "Delete Project", self)
        self.delete_proj_action.triggered.connect(self.on_delete_project)
        self.delete_proj_action.setEnabled(False)
        add_action(self.delete_proj_action)
        
        add_separator()
        
        self.close_proj_action_tb = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserStop), "Close Project", self)
        self.close_proj_action_tb.triggered.connect(self.on_close_project)
        self.close_proj_action_tb.setEnabled(False)
        add_action(self.close_proj_action_tb)
        
        layout.addStretch()
        scroll.setWidget(container)
        toolbar.addWidget(scroll)
        self.addToolBar(toolbar)

    def show_toast(self, message: str, duration: int = 5000, toast_type: str = "error"):
        """Displays a ToastNotification on the top right."""
        from app.branding import scrub
        message = scrub(message)
        if not hasattr(self, '_active_toasts'):
            self._active_toasts = []
        
        # Clean up old dead toasts (deleted ones raise RuntimeError)
        alive = []
        for t in self._active_toasts:
            try:
                if t.isVisible():
                    alive.append(t)
            except RuntimeError:
                pass
        self._active_toasts = alive
        
        toast = ToastNotification(self, message, duration, toast_type)
        self._active_toasts.append(toast)
        toast.show_toast()

    def log(self, message: str):
        from app.branding import scrub
        message = scrub(str(message))
        color = "#d4d4d4" # default white/grey
        msg_lower = message.lower()
        if "[error]" in msg_lower or "failed" in msg_lower:
            color = "#ff5555" # Red
        elif "[warn]" in msg_lower:
            color = "#ffaa00" # Orange
        elif "[success]" in msg_lower or "completed successfully" in msg_lower:
            color = "#55ff55" # Green
        elif "[info]" in msg_lower:
            color = "#ffffff" # White
            
        try:
            print(f"[ui] {message}")          # also into the session log file
        except Exception:
            pass
        safe_msg = message.replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
        html = f'<span style="color: {color};">{safe_msg}</span>'
        self.console.append(html)
        
        scrollbar = self.console.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def update_recent_projects_menu(self):
        self.recent_menu.clear()
        recent = self.controller.get_recent_projects()
        if not recent:
            action = QAction("No recent projects", self)
            action.setEnabled(False)
            self.recent_menu.addAction(action)
        else:
            for proj_path in recent:
                action = QAction(os.path.basename(proj_path), self)
                action.triggered.connect(lambda checked=False, p=proj_path: self.open_project_by_path(p))
                self.recent_menu.addAction(action)

    def on_upload_json(self):
        info = self.controller.get_current_project_info()
        if not info:
            QMessageBox.warning(self, "No Project", "Please open a project first.")
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Upload Semantic JSON",
            "",
            "JSON Files (*.json)"
        )
        if not file_path:
            return

        import json
        import shutil
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Validation logic
            if data.get('type') != 'document':
                raise ValueError("JSON must have 'type': 'document'")
            if 'metadata' not in data:
                raise ValueError("JSON must have 'metadata' object")
            if 'pages' not in data or not isinstance(data['pages'], list):
                raise ValueError("JSON must have 'pages' array")
            
            for page in data['pages']:
                if 'page_number' not in page:
                    raise ValueError("Each page must have a 'page_number'")
                if 'elements' not in page or not isinstance(page['elements'], list):
                    raise ValueError("Each page must have an 'elements' array")
                for el in page['elements']:
                    if 'type' not in el:
                        raise ValueError("Each element must have a 'type'")
                    if 'reading_order' not in el:
                        raise ValueError("Each element must have 'reading_order'")
                    if 'bounding_box' not in el or not isinstance(el['bounding_box'], list) or len(el['bounding_box']) != 4:
                        raise ValueError("Each element must have a 'bounding_box' array of 4 values")
            
            proj_path = info["path"]
            semantic_dir = os.path.join(proj_path, "semantic")
            os.makedirs(semantic_dir, exist_ok=True)
            
            dest_path = os.path.join(semantic_dir, "raw_semantic.json")
            shutil.copy2(file_path, dest_path)
            self.log(f"[SUCCESS] Uploaded and validated JSON to {dest_path}")
            QMessageBox.information(self, "Success", "JSON file successfully uploaded. The tagging pipeline will now run automatically!")
            self.on_pipeline_view_changed(0)
            self.on_generate_json()
        except Exception as e:
            self.log(f"[ERROR] JSON Validation failed: {str(e)}")
            QMessageBox.critical(self, "Validation Error", f"Invalid JSON structure:\n{str(e)}")

    def on_stop_pipeline(self):
        if self.worker and self.worker.isRunning():
            self.log("[INFO] Stopping pipeline... Finishing current page and caching progress.")
            self.stop_pipeline_action.setEnabled(False)
            self.progress_label.setText("Status: Stopping - finishing the current page...")
            self.worker.stop()

    def on_generate_json(self):
        info = self.controller.get_current_project_info()
        if not info: return
        if self.worker and self.worker.isRunning():
            self.show_toast("A pipeline / export is already running. Please wait for it to finish.", toast_type="error")
            return
        from engine.project_manager import DEMO_MAX_PAGES, count_pdf_pages
        try:
            n_pages = count_pdf_pages(info['input_pdf'])
        except Exception:
            n_pages = 0
        if n_pages > DEMO_MAX_PAGES:
            QMessageBox.information(self, "Demo Version - Page Limit",
                f"This project's PDF has {n_pages} pages. The demo version processes up to {DEMO_MAX_PAGES} pages.\n\n"
                f"Create a new project from this PDF: its first {DEMO_MAX_PAGES} pages will be used.")
            return
        self._pending_struct_deletes = [a for a in self.action_history.undo_stack
                                        if type(a).__name__ == "DeleteStructTagsAction"
                                        and getattr(a, "source", "output") != "input"]
        
        out_json = self.controller.get_semantic_json_path()
        if not out_json: return
        
        self.log("Starting Plugin Pipeline (Extraction + Engine)...")
        pass # Removed wait cursor
        self.generate_json_action.setEnabled(False)
        self.stop_pipeline_action.setEnabled(True)
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(0) # Indeterminate mode
        
        self.pipeline_seconds = 0
        self.timer_label.setText("Run time: 00:00")
        self.pipeline_timer.start(1000)
        
        # Force immediate UI redraw so the button click feels instantly responsive 
        # before the synchronous heavy engine imports block the main thread for a second.
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        
        from app.workers import PipelineWorker
        
        profile = info.get("profile") or {}
        document_type = self._effective_doc_type(profile)
        
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "engine.json")
        try:
            import json
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception as e:
            self.log(f"Failed to load engine.json: {e}")
            config = {}
            
        config["document_type"] = document_type
        config["project_metadata"] = self.controller.get_project_metadata()
        
        active_edits = [a for a in self.action_history.undo_stack
                        if type(a).__name__ != "DeleteStructTagsAction"]
        self.worker = PipelineWorker(info['path'], info['input_pdf'], config, active_edits)
        self.worker.finished_signal.connect(self.on_generate_json_finished)
        self.worker.progress_signal.connect(self.on_progress)
        self.worker.start()

    def on_progress(self, current: int, total: int, message: str):
        from app.branding import scrub
        message = scrub(message)
        self.progress_label.setText(f"Status: {message}")
        if total == 0:
            # Indeterminate loader mode (dynamic sliding animation)
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setFormat("")
        else:
            if self.progress_bar.maximum() == 0:
                self.progress_bar.setRange(0, 100)
            
            # Calculate percentage
            # If total == 100, current is a raw percentage.
            # Otherwise, current is a 1-based index (e.g. page 1/3), so subtract 1 to get completed units.
            if total == 100:
                percent = current
            else:
                percent = int((max(0, current - 1) / total) * 100)
            self.progress_bar.setValue(percent)
            self.progress_bar.setFormat("%p%")
        
        self.log(message)

    def on_live_update_finished(self, success: bool, message: str):
        from app.branding import scrub
        message = scrub(message)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("")
        self.progress_label.setText("Status: Ready")
        if success:
            self.log(message)
        else:
            self.log(f"PDF Update Failed: {message}")
            self.show_toast(message, duration=6000, toast_type="error")
            
        from PySide6.QtCore import QTimer
        QTimer.singleShot(200, self.populate_tags_tree)

    def on_fast_export_finished(self, success: bool, message: str, output_pdf: str = ""):
        from app.branding import scrub
        message = scrub(message)
        pass # Removed restore cursor
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("")
        self.progress_label.setText("Status: Ready")
        if success:
            self.log(f"Fast Export: {message}")
            self._after_export()
        else:
            self.log(f"Fast Export Failed: {message}")
            self.show_toast(message, duration=6000, toast_type="error")
            
        from PySide6.QtCore import QTimer
        QTimer.singleShot(200, self.populate_tags_tree)

    def _show_review_pages_notice(self, review: dict):
        """Tells the user once (after the run) which pages gave no elements
        after all extraction attempts."""
        from PySide6.QtWidgets import QMessageBox
        not_read = [str(p) for p in (review.get("not_read") or [])]
        if not not_read:
            return
        pages = not_read[0] if len(not_read) == 1 else ", ".join(not_read[:-1]) + " and " + not_read[-1]
        text = f"We can't extract the elements from Page {pages}.\n\nRun the pipeline again to retry."
        self.log(f"[WARNING] We can't extract the elements from Page {pages}.")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Extraction")
        box.setText(text)
        box.setStandardButtons(QMessageBox.Ok)
        box.exec()

    def on_generate_json_finished(self, success: bool, message: str, output_pdf: str = None):
        from app.branding import scrub
        message = scrub(message)
        pass # Removed restore cursor
        self.pipeline_timer.stop()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.progress_bar.setFormat("%p%")
        self.progress_label.setText("Complete: " + message if success else "Failed: " + message)
        self.generate_json_action.setEnabled(True)
        self.stop_pipeline_action.setEnabled(False)
        
        # Show native Windows notification using PySide6 System Tray
        try:
            from PySide6.QtWidgets import QSystemTrayIcon, QStyle
            if QSystemTrayIcon.isSystemTrayAvailable():
                icon = self._app_icon()
                if not hasattr(self, "tray_icon"):
                    self.tray_icon = QSystemTrayIcon(icon, self)
                    self.tray_icon.setToolTip("TagPilot")
                    self.tray_icon.activated.connect(lambda *_: (self.showNormal(), self.raise_(), self.activateWindow()))
                    self.tray_icon.show()
                # the notification shows the TagPilot icon (not the generic info/warning icon)
                self.tray_icon.showMessage("TagPilot - Pipeline", message, icon, 5000)
        except Exception as e:
            self.log(f"Notification error: {e}")
        
        if success:
            self.log(message)
            self._after_export()
            self.refresh_project_ui(reset_progress=False) # Refresh to show generated output pdf
            self.pipeline_combo.setCurrentIndex(8)
            self.on_pipeline_view_changed(8) # Load Rules V1 JSON by default to allow editing final tags
            self.show_toast("Pipeline completed successfully!", duration=4000, toast_type="success")
            review = getattr(self.sender(), "review_pages", None) or getattr(getattr(self, "worker", None), "review_pages", None)
            if review and review.get("not_read"):
                from PySide6.QtCore import QTimer
                QTimer.singleShot(400, lambda r=dict(review): self._show_review_pages_notice(r))
        else:
            self.log(f"Pipeline Execution Failed: {message}")
            self.show_toast(message, duration=7000, toast_type="error")
            
            if "quota" in message.lower() or "overloaded" in message.lower() or "unavailable" in message.lower():
                from PySide6.QtWidgets import QMessageBox
                reply = QMessageBox.question(self, "Extraction Paused", 
                    f"{message}\n\nWould you like to retry the pipeline now?",
                    QMessageBox.Retry | QMessageBox.Cancel, QMessageBox.Retry)
                
                if reply == QMessageBox.Retry:
                    # let the finished worker thread end before starting a new one
                    from PySide6.QtCore import QTimer
                    QTimer.singleShot(500, self.on_generate_json)

    def _after_export(self):
        """The tagged PDF was regenerated: re-apply Tags-panel deletions and the
        Document Properties (Title / Author)."""
        pending, self._pending_struct_deletes = self._pending_struct_deletes, []
        if pending:
            self._reapply_struct_deletions(pending)
        info = self.controller.get_current_project_info()
        if info:
            self._apply_document_properties(info['path'], quiet=True)

    def on_pipeline_view_changed(self, index: int):
        info = self.controller.get_current_project_info()
        if not info: return
        
        files = [
            "semantic/raw_semantic.json",
            "semantic/normalized_v1.json",
            "graph/geometry_v1.json",
            "graph/visual_v1.json",
            "graph/detected_v1.json",
            "graph/analysis_v1.json",
            "graph/consistency_v1.json",
            "graph/qa_v1.json",
            "graph/rules_v1.json"
        ]
        if index >= len(files): return
        
        target_file = os.path.join(info["path"], files[index])
        if not os.path.exists(target_file):
            self.log(f"File not found for this stage: {target_file}")
            self.all_elements = []
            self.action_history.clear()
            self._update_undo_redo_actions()
            self.page_viewer.load_elements(self.all_elements)
            self.populate_elements_tree()
            return
            
        self.load_semantic_json(target_file)

    def load_semantic_json(self, filepath: str):
        try:
            import json
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            self.all_elements = []
            self.action_history.clear()
            self._update_undo_redo_actions()
            
            if isinstance(data, list):
                self.all_elements.extend(data)
            elif 'nodes' in data and isinstance(data['nodes'], list):
                # V2 Document Graph Schema
                self.all_elements.extend(data['nodes'])
            elif 'pages' in data: # V1 Document Graph schema or raw JSON
                pages_data = data['pages']
                pages_iter = pages_data.values() if isinstance(pages_data, dict) else pages_data
                for page in pages_iter:
                    # Handle raw json schema vs graph schema
                    elems = page.get('elements', []) if 'elements' in page else page.get('nodes', [])
                    self.all_elements.extend(elems)
                    
            # Load manual edits if they exist
            info = self.controller.get_current_project_info()
            if info:
                edits = PersistenceManager.load_edits(info['path'], logger=self.log)
                for edit in edits:
                    self.action_history.execute(edit, self.all_elements)
                self.action_history.save_checkpoint()
                self._update_undo_redo_actions()
                if edits:
                    self.log(f"Replayed {len(edits)} manual edits.")
                
            self.page_viewer.load_elements(self.all_elements)
            self.log(f"Loaded JSON Stage: {os.path.basename(filepath)} ({len(self.all_elements)} elements).")
            self.populate_elements_tree()
        except Exception as e:
            self.log(f"Error loading JSON: {e}")

    def populate_elements_tree(self):
        print("[ElementsTree] Populating elements tree...")
        import sys
        sys.stdout.flush()
        # 1. Populate original Elements Tree
        print("[ElementsTree] Clearing elements tree...")
        sys.stdout.flush()
        
        # Save expanded state
        self._expanded_elements = set()
        def get_elem_path(node):
            path = []
            current = node
            while current:
                parent = current.parent()
                idx = parent.indexOfChild(current) if parent else self.elements_tree.indexOfTopLevelItem(current)
                path.insert(0, f"{current.text(0)}[{idx}]")
                current = parent
            return "/".join(path)

        def collect_expanded_elems(node):
            if node.isExpanded():
                self._expanded_elements.add(get_elem_path(node))
            for i in range(node.childCount()):
                collect_expanded_elems(node.child(i))
        
        for i in range(self.elements_tree.topLevelItemCount()):
            collect_expanded_elems(self.elements_tree.topLevelItem(i))
            
        current = self.elements_tree.currentItem()
        if current:
            self.elements_tree.closePersistentEditor(current)
            
        self.elements_tree.clearSelection()
        self.elements_tree.setCurrentItem(None)
        self.elements_tree.clearFocus()
        
        if not hasattr(self, '_dead_items'):
            self._dead_items = []
            
        while self.elements_tree.topLevelItemCount() > 0:
            self._dead_items.append(self.elements_tree.takeTopLevelItem(0))
            
        from PySide6.QtCore import QTimer
        QTimer.singleShot(2000, self._dead_items.clear)
        
        print("[ElementsTree] Organizing pages...")
        sys.stdout.flush()
        pages = {}
        for el in self.all_elements:
            p_val = el.get('metadata', {}).get('page') or el.get('page_number', 1)
            try: p = int(p_val)
            except: p = 1
            if p not in pages: pages[p] = []
            pages[p].append(el)
            
        print("[ElementsTree] Creating tree nodes...")
        sys.stdout.flush()
        for p in sorted(pages.keys()):
            page_node = QTreeWidgetItem(self.elements_tree, [f"Page {p}"])
            page_node.setData(0, Qt.UserRole + 2, pages[p]) # Store elements data
            # Add a dummy child to show the expand arrow
            dummy = QTreeWidgetItem(page_node, ["Loading..."])
            
            if hasattr(self, '_expanded_elements'):
                if f"Page {p}[{self.elements_tree.indexOfTopLevelItem(page_node)}]" in self._expanded_elements:
                    page_node.setExpanded(True)
            
        print("[ElementsTree] Calling populate_tags_tree...")
        sys.stdout.flush()
        self.populate_tags_tree()

    def _on_elements_tree_expanded(self, item):
        elements = item.data(0, Qt.UserRole + 2)
        if elements is not None:
            # Clear dummy
            item.takeChildren()
            item.setData(0, Qt.UserRole + 2, None) # Remove data so we don't reload
            p = item.text(0).replace("Page ", "")
            for el in elements:
                if 'role' in el: type_str = el['role']
                elif 'semantic' in el: type_str = el['semantic'].get('type', 'Unknown')
                else: type_str = el.get('semantic_type', el.get('type', 'Unknown'))
                    
                id_str = str(el.get('id') or el.get('uuid') or el.get('node_id') or '')
                el_node = QTreeWidgetItem(item, [type_str, id_str, str(p)])
                el_node.setData(0, Qt.ItemDataRole.UserRole, el)

    def populate_tags_tree(self):
        print("[TagsTree] populating tags tree...")
        import sys
        sys.stdout.flush()
        # Clean up dead threads
        if hasattr(self, 'worker') and self.worker and self.worker.isFinished():
            self.worker = None
        if hasattr(self, 'live_pdf_worker') and self.live_pdf_worker and self.live_pdf_worker.isFinished():
            self.live_pdf_worker = None
            
        # Prevent concurrent read/write crashes (C++ segfaults) if background workers are modifying the PDF
        if hasattr(self, 'worker') and self.worker and self.worker.isRunning():
            print("[TagsTree] Aborting: worker is running")
            return
        if hasattr(self, 'live_pdf_worker') and self.live_pdf_worker and getattr(self.live_pdf_worker, 'is_processing', False):
            print("[TagsTree] Aborting: live_pdf_worker is processing")
            return
            
        print("[TagsTree] Blocking signals...")
        sys.stdout.flush()
        self.tags_tree.blockSignals(True)
        try:
            print("[TagsTree] Clearing tree...")
            sys.stdout.flush()
            
            # Save expanded state
            self._expanded_tags = set()
            def get_node_path(node):
                path = []
                current = node
                while current:
                    parent = current.parent()
                    idx = parent.indexOfChild(current) if parent else self.tags_tree.indexOfTopLevelItem(current)
                    path.insert(0, f"{current.text(0)}[{idx}]")
                    current = parent
                return "/".join(path)

            def collect_expanded(node):
                if node.isExpanded():
                    self._expanded_tags.add(get_node_path(node))
                for i in range(node.childCount()):
                    collect_expanded(node.child(i))
            
            for i in range(self.tags_tree.topLevelItemCount()):
                collect_expanded(self.tags_tree.topLevelItem(i))
                
            print(f"[TagsTree] Saved {len(self._expanded_tags)} expanded paths: {self._expanded_tags}")
            sys.stdout.flush()
            
            current = self.tags_tree.currentItem()
            if current:
                self.tags_tree.closePersistentEditor(current)
                
            self.tags_tree.clearSelection()
            self.tags_tree.setCurrentItem(None)
            self.tags_tree.clearFocus()
            
            # Use deferred destruction pattern for PySide6 segfaults
            if not hasattr(self, '_dead_items'):
                self._dead_items = []
                
            while self.tags_tree.topLevelItemCount() > 0:
                self._dead_items.append(self.tags_tree.takeTopLevelItem(0))
                
            from PySide6.QtCore import QTimer
            QTimer.singleShot(2000, self._dead_items.clear)
            
            print("[TagsTree] Creating doc root...")
            sys.stdout.flush()
            doc_root = QTreeWidgetItem(self.tags_tree, ["<Document>"])
            
            print("[TagsTree] Getting project info...")
            sys.stdout.flush()
            info = self.controller.get_current_project_info()
            if not info: return
            
            pdf_path = info.get("input_pdf")
            
            output_dir = os.path.join(info['path'], "output")
            if os.path.exists(output_dir):
                pdfs = [f for f in os.listdir(output_dir) if f.lower().endswith('.pdf')]
                if pdfs:
                    # Prioritize tagged output
                    tagged = [f for f in pdfs if 'tagged' in f.lower()]
                    if tagged:
                        pdf_path = os.path.join(output_dir, tagged[0])
                    else:
                        pdf_path = os.path.join(output_dir, pdfs[0])
                        
            if not pdf_path or not os.path.exists(pdf_path):
                print(f"[TagsTree] File not found: {pdf_path}")
                QTreeWidgetItem(doc_root, ["File not found."])
                doc_root.setExpanded(True)
                return
            
            print(f"[TagsTree] Opening PDF: {pdf_path}")
            sys.stdout.flush()
            import pikepdf
            try:
                with open(pdf_path, "rb") as f:
                    pdf_bytes = f.read()
            except PermissionError:
                print(f"[TagsTree] Cannot open PDF (locked): {pdf_path}")
                QTreeWidgetItem(doc_root, ["Cannot load tags (File open in Acrobat)"])
                doc_root.setExpanded(True)
                return
            except Exception as e:
                print(f"[TagsTree] Cannot open PDF (error): {e}")
                QTreeWidgetItem(doc_root, [f"Error loading tags: {e}"])
                doc_root.setExpanded(True)
                return
                
            try:
                import io
                with pikepdf.Pdf.open(io.BytesIO(pdf_bytes)) as pdf:
                    struct_tree_root = pdf.Root.get("/StructTreeRoot")
                    if not struct_tree_root:
                        print("[TagsTree] No /StructTreeRoot found.")
                        QTreeWidgetItem(doc_root, ["No Tags available."])
                        doc_root.setExpanded(True)
                        return
                    print("[TagsTree] Found /StructTreeRoot.")
                    
                    # Load final stage JSON for Tags Tree lookup to resolve injected nodes (Lbl, LBody, etc.)
                    final_elements = self.all_elements
                    rules_json = os.path.join(info['path'], "graph", "rules_v1.json")
                    if os.path.exists(rules_json):
                        try:
                            import json
                            with open(rules_json, 'r', encoding='utf-8') as f:
                                rules_data = json.load(f)
                            if 'nodes' in rules_data:
                                final_elements = rules_data['nodes']
                                for action in self.action_history.undo_stack:
                                    action.apply(final_elements)
                            elif 'pages' in rules_data:
                                final_elements = []
                                for page in rules_data.get('pages', []):
                                    final_elements.extend(page.get('elements', []) if 'elements' in page else page.get('nodes', []))
                                for action in self.action_history.undo_stack:
                                    action.apply(final_elements)
                        except Exception as e:
                            self.log(f"Error loading rules_v1.json for tags lookup: {e}")
                            
                    el_lookup = {}
                    def add_to_lookup(elements_list):
                        for el in elements_list:
                            e_id = str(el.get('id') or el.get('uuid') or el.get('node_id'))
                            if e_id and e_id != 'None':
                                el_lookup[e_id] = el
                            if 'kids' in el:
                                add_to_lookup(el['kids'])
                    add_to_lookup(final_elements)
                    
                    if not hasattr(self, '_tag_icon'):
                        from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QPolygon, QBrush
                        from PySide6.QtCore import QPoint
                        from PySide6.QtGui import QIcon
                        pix = QPixmap(16, 16)
                        pix.fill(Qt.transparent)
                        p = QPainter(pix)
                        p.setRenderHint(QPainter.Antialiasing)
                        p.setPen(QPen(QColor(120, 100, 60), 1))
                        p.setBrush(QBrush(QColor(210, 180, 120)))
                        p.drawPolygon(QPolygon([QPoint(2, 8), QPoint(6, 2), QPoint(14, 2), QPoint(14, 14), QPoint(6, 14)]))
                        p.setBrush(QBrush(Qt.white))
                        p.drawEllipse(10, 7, 3, 3)
                        p.end()
                        self._tag_icon = QIcon(pix)
                        
                    def parse_element(parent_widget, el_dict, inherited_el=None, depth=0, path=()):
                        if depth > 100:
                            return
                            
                        if el_dict is None:
                            return
                            
                        try:
                            s_type = str(el_dict.get("/S", "Unknown"))
                        except Exception as e:
                            QTreeWidgetItem(parent_widget, [f"<Error: Cannot read /S: {e}>"])
                            return
                            
                        tag_name = s_type[1:] if s_type.startswith("/") else s_type
                        
                        if tag_name == "Document" and parent_widget == doc_root:
                            node = parent_widget
                        else:
                            node = QTreeWidgetItem(parent_widget, [f"<{tag_name}>"])
                            node.setIcon(0, self._tag_icon)
                        # Identity of this tag in the PDF (used by Delete Tag)
                        node.setData(0, Qt.UserRole + 3, list(path))
                        node.setData(0, Qt.UserRole + 4, s_type)
                        try:
                            _ref = el_dict.get("/TPNodeID")
                            node.setData(0, Qt.UserRole + 5, str(_ref) if _ref is not None else None)
                        except Exception:
                            node.setData(0, Qt.UserRole + 5, None)
                        
                        if hasattr(self, '_expanded_tags'):
                            path = []
                            current = node
                            while current:
                                parent = current.parent()
                                idx = parent.indexOfChild(current) if parent else self.tags_tree.indexOfTopLevelItem(current)
                                path.insert(0, f"{current.text(0)}[{idx}]")
                                current = parent
                            node_path = "/".join(path)
                            if node_path in self._expanded_tags:
                                node.setExpanded(True)
                            if tag_name in ("L", "Table", "LI", "Lbl"):
                                pass # print(f"DEBUG Tag: {node_path}, Is in expanded? {node_path in self._expanded_tags}")
                        
                        try:
                            el_id_str = str(el_dict.get("/TPNodeID") or el_dict.get("/ID", ""))
                        except Exception:
                            el_id_str = ""
                            
                        el = None
                        if el_id_str and el_id_str != "None":
                            clean_id = el_id_str.replace("node_", "")
                            el = el_lookup.get(clean_id)
                            if el:
                                node.setData(0, Qt.ItemDataRole.UserRole, el)
                                
                        effective_el = el or inherited_el
                        
                        try:
                            k = el_dict.get("/K")
                        except Exception:
                            k = None
                            
                        kids = []
                        has_mcid = False
                        
                        def process_kid(kid_obj, kid_index=0):
                            nonlocal has_mcid
                            try:
                                if isinstance(kid_obj, int):
                                    has_mcid = True
                                    return
                                    
                                kid_type = kid_obj.get("/Type")
                                if kid_type != "/MCR" and kid_type != "/OBJR":
                                    kids.append((kid_index, kid_obj))
                                elif kid_type == "/MCR":
                                    has_mcid = True
                            except Exception as ex:
                                print(f"[TagsTree DEBUG]   -> process_kid EXCEPTION on {kid_obj}: {ex}")
                                pass
    
                        try:
                            if isinstance(k, pikepdf.Array):
                                for i in range(len(k)):
                                    process_kid(k[i], i)
                            elif isinstance(k, pikepdf.Dictionary) or isinstance(k, pikepdf.Object):
                                process_kid(k, 0)
                            elif isinstance(k, int):
                                has_mcid = True
                        except Exception as e:
                            QTreeWidgetItem(node, [f"<Error reading kids: {e}>"])
                        
                        try:
                            actual_text = el_dict.get("/ActualText")
                        except Exception:
                            actual_text = None
                            
                        try:
                            alt_text = el_dict.get("/Alt")
                        except Exception:
                            alt_text = None
                        
                        if actual_text:
                            text_node = QTreeWidgetItem(node, [str(actual_text)])
                            if effective_el: text_node.setData(0, Qt.ItemDataRole.UserRole, effective_el)
                            text_node.setData(0, Qt.UserRole + 1, "is_text")
                        elif alt_text:
                            text_node = QTreeWidgetItem(node, [f"Alt: {str(alt_text)}"])
                            if effective_el: text_node.setData(0, Qt.ItemDataRole.UserRole, effective_el)
                            text_node.setData(0, Qt.UserRole + 1, "is_text")
                        elif effective_el and (el is not None or has_mcid or tag_name == "Figure"):
                            text = effective_el.get('text') or effective_el.get('content') or effective_el.get('semantic', {}).get('content') or ''
                            if tag_name == "Figure" and effective_el.get('image_path'):
                                text = "Path: " + str(effective_el.get('image_path'))
                            if text or has_mcid:
                                text_node = QTreeWidgetItem(node, [text or "(Empty)"])
                                text_node.setFlags(text_node.flags() | Qt.ItemIsEditable)
                                text_node.setData(0, Qt.ItemDataRole.UserRole, effective_el)
                                text_node.setData(0, Qt.UserRole + 1, "is_text")

                                
                        for kid_index, kid in kids:
                            parse_element(node, kid, effective_el, depth + 1, tuple(path) + (kid_index,))
                        
                    # <Document> row = whole tag tree until a /Document element claims it
                    doc_root.setData(0, Qt.UserRole + 3, [])
                    doc_root.setData(0, Qt.UserRole + 4, None)
                    doc_root.setData(0, Qt.UserRole + 5, None)
                    k_root = struct_tree_root.get("/K")
                    if isinstance(k_root, pikepdf.Array):
                        for i in range(len(k_root)):
                            el_dict = k_root[i]
                            if el_dict is not None:
                                parse_element(doc_root, el_dict, path=(i,))
                    elif isinstance(k_root, pikepdf.Dictionary):
                        parse_element(doc_root, k_root, path=(0,))
                    elif k_root is not None:
                        parse_element(doc_root, k_root, path=(0,))
                        
                    doc_root.setExpanded(True)
            except Exception as e:
                print(f"[TagsTree] Failed to parse PDF structure: {e}")
                QTreeWidgetItem(doc_root, ["Error parsing PDF structure."])
                doc_root.setExpanded(True)
        except Exception as e:
            self.log(f"Failed to parse PDF tags: {e}")
            QTreeWidgetItem(doc_root, [f"Error: {str(e)}"])
            doc_root.setExpanded(True)
        finally:
            self.tags_tree.blockSignals(False)
    def on_element_selected(self):
        selected = self.elements_tree.selectedItems()
        if not selected:
            return
            
        item = selected[0]
        el = item.data(0, Qt.ItemDataRole.UserRole)
        if not el:
            return
            
        el_id = el.get('id') or el.get('uuid') or el.get('node_id')
        p_num = el.get('page_number')
        if not p_num:
            p_num = el.get('metadata', {}).get('page')
            
        if el_id and p_num:
            self.page_viewer.select_element(el_id, p_num)
            self._sync_tree_selection(el_id, exclude_tree=self.elements_tree)
            
        prop_text = "<h2>Element Properties</h2>"
        for k, v in el.items():
            if k == 'box_2d': continue
            prop_text += f"<p><b>{k}:</b> {str(v)}</p>"
        self.property_panel.setText(prop_text)
        
        # Update History
        self.history_panel.clear()
        if 'history' in el:
            for history_item in el['history']:
                self.history_panel.addItem(history_item)
        else:
            self.history_panel.addItem("No history available for this node.")

    def on_tag_tree_selected(self):
        selected = self.tags_tree.selectedItems()
        if not selected:
            return
            
        item = selected[0]
        el = item.data(0, Qt.ItemDataRole.UserRole)
        
        all_el_ids = []
        extra_elements = []
        p_num = None
        
        def gather_from_tree(node):
            nonlocal p_num
            node_el = node.data(0, Qt.ItemDataRole.UserRole)
            if node_el:
                el_id = node_el.get('id') or node_el.get('uuid') or node_el.get('node_id')
                
                # Fetch freshest element data from self.all_elements if available
                fresh_el = node_el
                if el_id and hasattr(self, 'all_elements'):
                    for fel in self.all_elements:
                        fid = str(fel.get('id') or fel.get('uuid') or fel.get('node_id'))
                        if fid == str(el_id):
                            fresh_el = fel
                            break
                            
                sem_type = fresh_el.get('semantic', {}).get('type') or fresh_el.get('type')
                
                # Acrobat-style highlighting: skip wrapper boxes, only highlight content leaves
                if sem_type not in ('table', 'table_row', 'list', 'list_item'):
                    extra_elements.append(fresh_el)
                    if el_id:
                        all_el_ids.append(el_id)
                        
                if not p_num:
                    p_num = fresh_el.get('page_number') or fresh_el.get('metadata', {}).get('page')
            
            for i in range(node.childCount()):
                gather_from_tree(node.child(i))
                
        gather_from_tree(item)
            
        if p_num:
            self.page_viewer.select_element(all_el_ids, p_num, extra_elements=extra_elements)
            if all_el_ids:
                self._sync_tree_selection(all_el_ids[0], exclude_tree=self.tags_tree)
            
        if el:
            # Refresh el to freshest for the properties panel too!
            el_id = el.get('id') or el.get('uuid') or el.get('node_id')
            if el_id and hasattr(self, 'all_elements'):
                for fel in self.all_elements:
                    fid = str(fel.get('id') or fel.get('uuid') or fel.get('node_id'))
                    if fid == str(el_id):
                        el = fel
                        break
            prop_text = "<h2>Tag Properties</h2>"
            for k, v in el.items():
                if k == 'box_2d': continue
                prop_text += f"<p><b>{k}:</b> {str(v)}</p>"
            self.property_panel.setText(prop_text)
            
            self.history_panel.clear()
            if 'history' in el:
                for history_item in el['history']:
                    self.history_panel.addItem(history_item)
            else:
                self.history_panel.addItem("No history available for this node.")
        else:
            self.property_panel.setText("<h2>Tag Properties</h2><p>Grouping Node</p>")
            self.history_panel.clear()

    def on_tag_text_changed(self, item, column):
        if item.data(0, Qt.UserRole + 1) == "is_text":
            el = item.data(0, Qt.ItemDataRole.UserRole)
            if not el: return
            new_text = item.text(column)
            el_id = el.get('id') or el.get('uuid') or el.get('node_id')
            if el_id:
                old_text = el.get('text') or el.get('content') or ''
                if new_text != old_text:
                        from gui.edit_actions import UpdateTextAction
                        
                        action = UpdateTextAction(el_id, new_text, old_text)
                        if self.action_history.execute(action, self.all_elements):
                            from PySide6.QtCore import QTimer
                            QTimer.singleShot(0, self.populate_elements_tree)
                            self._update_undo_redo_actions()
                            self.schedule_live_pdf_update()


    def _block_if_running(self, what: str) -> bool:
        """True (and a message) when a pipeline / export is running."""
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Pipeline Running",
                f"A pipeline is running for the current project.\n\nStop it (Stop Pipeline) and wait until it finishes before you {what}.")
            return True
        return False

    def on_new_project(self):
        if self._block_if_running("create a new project"):
            return
        pdf_path, _ = QFileDialog.getOpenFileName(self, "Select Source PDF", "", "PDF Files (*.pdf)")
        if not pdf_path:
            return
            
        project_name = os.path.splitext(os.path.basename(pdf_path))[0]

        from engine.project_manager import DEMO_MAX_PAGES, count_pdf_pages
        existing = os.path.join(str(self.controller.project_manager.workspace_dir), project_name)
        if os.path.exists(existing):
            QMessageBox.critical(self, "Error", f"Project '{project_name}' already exists in workspace.")
            return
        try:
            page_count = count_pdf_pages(pdf_path)
        except Exception as e:
            QMessageBox.critical(self, "Cannot Open PDF",
                f"This PDF cannot be opened (it may be damaged or password-protected).\n\n{e}")
            return
        max_pages = None
        if page_count > DEMO_MAX_PAGES:
            reply = QMessageBox.question(
                self, "Demo Version - Page Limit",
                f"This PDF has {page_count} pages.\n\n"
                f"The demo version can process up to {DEMO_MAX_PAGES} pages, so only the "
                f"first {DEMO_MAX_PAGES} pages (1-{DEMO_MAX_PAGES}) will be used.\n\n"
                f"Create the project with the first {DEMO_MAX_PAGES} pages?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if reply != QMessageBox.Yes:
                self.statusBar.showMessage("Project not created.", 5000)
                return
            max_pages = DEMO_MAX_PAGES
        
        try:
            self.controller.create_project(project_name, pdf_path, max_pages=max_pages)
            if max_pages:
                self.log(f"Created project: {project_name} (first {max_pages} of {page_count} pages)")
            else:
                self.log(f"Created project: {project_name}")
            self.refresh_project_ui()
            self.update_recent_projects_menu()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            self.log(f"Error creating project: {e}")

    def on_open_project(self):
        proj_dir = QFileDialog.getExistingDirectory(self, "Select Project Directory",
                                                    str(self.controller.project_manager.workspace_dir))
        if not proj_dir:
            return
        self.open_project_by_path(proj_dir)

    def open_project_by_path(self, proj_dir: str):
        if self._block_if_running("open another project"):
            return
        if not self.check_dirty_state():
            return
        try:
            self.controller.open_project(proj_dir)
            self.log(f"Opened project: {os.path.basename(proj_dir)}")
            self.refresh_project_ui()
            self.update_recent_projects_menu()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            self.log(f"Error opening project: {e}")

    def on_close_project(self):
        if self._block_if_running("close the project"):
            return False
        if not self.check_dirty_state():
            return
        self.controller.close_project()
        self.action_history.clear()
        self._pending_struct_deletes = []
        self.log("Project closed.")
        self.refresh_project_ui()
        return True

    def on_tag_content_toggled(self, checked):
        if not self.controller.get_current_project_info():
            return

        self.page_viewer.tagging_mode = checked
        if checked:
            # Enter Tag Content mode: crosshair cursor, disable scroll-hand drag
            self.page_viewer.view.setDragMode(QGraphicsView.NoDrag)
            self.page_viewer.view.setCursor(Qt.CrossCursor)
            self.statusBar.showMessage(
                "Tag Content mode ON — draw a rectangle over native text. "
                "P=Paragraph  H+1-6=Heading  Enter=dialog  ESC=cancel/exit"
            )
        else:
            # Exit Tag Content mode: clear any active selection, restore pan cursor
            if hasattr(self.page_viewer.view, '_clear_active_selection'):
                self.page_viewer.view._clear_active_selection()
                self.page_viewer.view._clear_rubber_band()
            self.page_viewer.view.setDragMode(QGraphicsView.ScrollHandDrag)
            self.page_viewer.view.setCursor(Qt.OpenHandCursor)
            self.statusBar.showMessage("Ready")

    # ------------------------------------------------------------------
    # PDF Type: shown from the profile's "Digital Text" (Yes -> Digital PDF,
    # No -> Scanned PDF) unless the user picked a type for this project.
    # ------------------------------------------------------------------
    @staticmethod
    def _detected_doc_type(profile: dict) -> str:
        """Engine type from the profile's content flags: DIGITAL / HYBRID / SCANNED."""
        content = (profile or {}).get("content") or {}
        if "has_digital_text" in content:
            if content.get("has_digital_text"):
                return "HYBRID" if content.get("has_images") else "DIGITAL"
            return "SCANNED"
        dt = str((profile or {}).get("document_type") or "").upper()
        if "HYBRID" in dt:
            return "HYBRID"
        if "DIGITAL" in dt:
            return "DIGITAL"
        return "SCANNED"

    def _doc_type_label(self, profile: dict) -> str:
        override = (profile or {}).get("document_type_override")
        if override in ("Digital PDF", "Scanned PDF"):
            return override
        return "Scanned PDF" if self._detected_doc_type(profile) == "SCANNED" else "Digital PDF"

    def _effective_doc_type(self, profile: dict) -> str:
        """document_type passed to the pipeline (keeps HYBRID handling for digital
        PDFs that also contain images)."""
        detected = self._detected_doc_type(profile)
        override = (profile or {}).get("document_type_override")
        if override == "Scanned PDF":
            return "Scanned PDF"
        if override == "Digital PDF":
            return detected if detected in ("DIGITAL", "HYBRID") else "Digital PDF"
        return detected

    def on_doc_type_changed(self, text: str):
        info = self.controller.get_current_project_info()
        if not info: return
        profile = info.get("profile") or {}
        auto = "Scanned PDF" if self._detected_doc_type(profile) == "SCANNED" else "Digital PDF"
        if text == auto:
            profile.pop("document_type_override", None)   # back to automatic
        else:
            profile["document_type_override"] = text      # the user's choice
        
        # Save back to profile.json
        import json
        profile_path = os.path.join(info["path"], "profile.json")
        try:
            with open(profile_path, "w", encoding="utf-8") as f:
                json.dump(profile, f, indent=4)
        except Exception as e:
            self.log(f"Error saving profile: {e}")
            
        self.log(f"Document type set to: {text}")

    def refresh_project_ui(self, reset_progress=True):
        info = self.controller.get_current_project_info()
        
        if reset_progress:
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("")
            self.progress_label.setText("Status: Ready")
        
        if not hasattr(self, '_dead_items'):
            self._dead_items = []
            
        # Safely clear project explorer
        self.project_explorer.clearSelection()
        self.project_explorer.setCurrentItem(None)
        self.project_explorer.clearFocus()
        while self.project_explorer.topLevelItemCount() > 0:
            self._dead_items.append(self.project_explorer.takeTopLevelItem(0))
            
        # Safely clear elements tree
        self.elements_tree.clearSelection()
        self.elements_tree.setCurrentItem(None)
        self.elements_tree.clearFocus()
        while self.elements_tree.topLevelItemCount() > 0:
            self._dead_items.append(self.elements_tree.takeTopLevelItem(0))
            
        # Safely clear tags tree
        self.tags_tree.clearSelection()
        self.tags_tree.setCurrentItem(None)
        self.tags_tree.clearFocus()
        while self.tags_tree.topLevelItemCount() > 0:
            self._dead_items.append(self.tags_tree.takeTopLevelItem(0))
            
        from PySide6.QtCore import QTimer
        QTimer.singleShot(2000, self._dead_items.clear)
            
        self.all_elements = []
        if hasattr(self.page_viewer.view, '_clear_active_selection'):
            self.page_viewer.view._clear_active_selection()
            self.page_viewer.view._clear_rubber_band()
        self.page_viewer.scene.clear()
        self.page_viewer.current_page_idx = 0
        self.page_viewer.update_controls()
        
        if info:
            self.setWindowTitle(f"TagPilot - {info['name']}")
            
            root = QTreeWidgetItem([f"Project: {info['name']}"])
            files = QTreeWidgetItem(root, ["Files"])
            
            # File nodes
            pdf_node = QTreeWidgetItem(files, ["📄 input.pdf"])
            pdf_node.setToolTip(0, info['input_pdf'])
            
            # Check for output PDF
            output_dir = os.path.join(info['path'], "output")
            if os.path.exists(output_dir):
                for fname in os.listdir(output_dir):
                    if fname.lower().endswith('.pdf'):
                        out_path = os.path.join(output_dir, fname)
                        out_node = QTreeWidgetItem(files, [f"📄 {fname}"])
                        out_node.setToolTip(0, out_path)
            
            self.project_explorer.addTopLevelItem(root)
            self.project_explorer.expandAll()
            
            self.statusBar.showMessage(f"Project '{info['name']}' loaded.")
            
            # Update doc type combo (Digital Text: Yes -> Digital PDF, No -> Scanned PDF)
            profile = info.get("profile") or {}
            doc_type = self._doc_type_label(profile)
            self.doc_type_combo.blockSignals(True)
            self.doc_type_combo.setCurrentText(doc_type)
            self.doc_type_combo.setEnabled(True)
            self.doc_type_combo.blockSignals(False)
            
            # Formatted properties string
            prop_text = (
                f"<h2>Project Info</h2>"
                f"<p><b>Name:</b> {info['name']}</p>"
                f"<p><b>Path:</b><br/> <span style='color:#0078d7'>{info['path']}</span></p>"
                f"<hr/>"
                f"<p><b>Input PDF:</b><br/> <span style='color:#0078d7'>{info['input_pdf']}</span></p>"
            )
            
            profile = info.get("profile")
            if profile:
                prop_text += "<hr/><h3>Document Profile</h3>"
                prop_text += "<table width='100%' style='font-size: 11px;'>"
                
                def add_row(key, val):
                    return f"<tr><td style='color:#aaaaaa;'>{key}</td><td style='text-align:right;'>{val}</td></tr>"
                    
                c = profile.get("core", {})
                a = profile.get("accessibility", {})
                content = profile.get("content", {})
                L = profile.get("layout", {})
                
                prop_text += add_row("PDF Version", c.get("pdf_version"))
                prop_text += add_row("Pages", c.get("page_count"))
                prop_text += add_row("Encrypted", "Yes" if c.get("is_encrypted") else "No")
                prop_text += add_row("Linearized", "Yes" if c.get("is_linearized") else "No")
                
                prop_text += "<tr><td colspan='2'><b>Content</b></td></tr>"
                prop_text += add_row("Digital Text", "Yes" if content.get("has_digital_text") else "No")
                prop_text += add_row("Images", "Yes" if content.get("has_images") else "No")
                prop_text += add_row("Vectors", "Yes" if content.get("has_vectors") else "No")
                prop_text += add_row("OCR Layer", "Yes" if content.get("has_ocr_layer") else "No")
                
                prop_text += "<tr><td colspan='2'><b>Accessibility</b></td></tr>"
                prop_text += add_row("Tagged PDF", "Yes" if a.get("is_tagged") else "No")
                prop_text += add_row("StructTree", "Yes" if a.get("has_struct_tree") else "No")
                prop_text += add_row("ParentTree", "Yes" if a.get("has_parent_tree") else "No")
                prop_text += add_row("AcroForms", "Yes" if a.get("has_acroforms") else "No")
                prop_text += add_row("Annotations", "Yes" if a.get("has_annotations") else "No")
                
                prop_text += "<tr><td colspan='2'><b>Layout & Transform</b></td></tr>"
                mixed = "Yes" if L.get("mixed_orientation") else "No"
                prop_text += add_row("Mixed Orientation", mixed)
                
                global_ctms = L.get("pages_with_global_ctm", [])
                if global_ctms:
                    prop_text += add_row("Global CTMs", f"Pages {global_ctms}")
                else:
                    prop_text += add_row("Global CTMs", "None")
                    
                prop_text += "</table>"

            self.property_panel.setText(prop_text)
            
            # Enable project actions
            self.generate_json_action.setEnabled(True)
            self.upload_json_action.setEnabled(True)
            self.download_pdf_action.setEnabled(True)
            self.delete_proj_action.setEnabled(True)
            self.doc_props_action.setEnabled(True)
            if hasattr(self, 'close_proj_action_menu'):
                self.close_proj_action_menu.setEnabled(True)
            self.close_proj_action_tb.setEnabled(True)
            self.tag_content_action.setEnabled(True)
            
            # Load the PDF into the viewer
            self.page_viewer.load_pdf(info['input_pdf'])
            
            # Load latest available JSON
            latest_idx = 0
            files = [
                "semantic/raw_semantic.json",
                "semantic/normalized_v1.json",
                "graph/geometry_v1.json",
                "graph/visual_v1.json",
                "graph/detected_v1.json",
                "graph/analysis_v1.json",
                "graph/consistency_v1.json",
                "graph/qa_v1.json",
                "graph/rules_v1.json"
            ]
            for i in range(len(files)-1, -1, -1):
                if os.path.exists(os.path.join(info["path"], files[i])):
                    latest_idx = i
                    break
            self.pipeline_combo.setCurrentIndex(latest_idx)
            self.on_pipeline_view_changed(latest_idx)
        else:
            self.setWindowTitle("TagPilot")
            root = QTreeWidgetItem(["Workspace: No project open"])
            self.project_explorer.addTopLevelItem(root)
            self.statusBar.showMessage("Ready")
            self.property_panel.setText("No Element Selected")
            self.generate_json_action.setEnabled(False)
            self.upload_json_action.setEnabled(False)
            self.download_pdf_action.setEnabled(False)
            self.delete_proj_action.setEnabled(False)
            self.doc_props_action.setEnabled(False)
            if hasattr(self, 'close_proj_action_menu'):
                self.close_proj_action_menu.setEnabled(False)
            self.close_proj_action_tb.setEnabled(False)
            self.page_viewer.scene.clear()
            self.page_viewer.doc = None
            self.page_viewer.update_controls()
            self.elements_tree.clear()



    def on_download_pdf(self):
        import shutil
        from PySide6.QtWidgets import QFileDialog
        
        info = self.controller.get_current_project_info()
        if not info: return
        
        workspace_name = os.path.basename(info['path'])
        output_pdf = os.path.join(info['path'], "output", f"{workspace_name}_tagged.pdf")
        
        if not os.path.exists(output_pdf):
            QMessageBox.warning(self, "Not Found", "The tagged PDF has not been generated yet. Please run the pipeline first.")
            return
            
        save_path, _ = QFileDialog.getSaveFileName(self, "Save Tagged PDF", f"{workspace_name}_tagged.pdf", "PDF Files (*.pdf)")
        if save_path:
            try:
                shutil.copy2(output_pdf, save_path)
                QMessageBox.information(self, "Success", f"PDF successfully downloaded to:\n{save_path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to download PDF:\n{e}")

    def on_delete_project(self):
        info = self.controller.get_current_project_info()
        if not info: return
        if self._block_if_running("delete the project"):
            return
        
        reply = QMessageBox.question(
            self, "Delete Project", 
            f"Are you sure you want to permanently delete the project '{info['name']}'?\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            import shutil
            path = info['path']
            name = info['name']
            
            # Check for file locks before attempting deletion
            from engine.file_lock import is_locked
            locked_files = []
            for root, dirs, files in os.walk(path):
                for file in files:
                    # only files really open in another program (not read-only / other errors)
                    if is_locked(os.path.join(root, file), retries=2):
                        locked_files.append(file)
                        
            if locked_files:
                QMessageBox.critical(
                    self, "Files in Use", 
                    f"Cannot delete the project because the following files are open in another program:\n\n"
                    f"{', '.join(locked_files[:5])}{' ...' if len(locked_files) > 5 else ''}\n\n"
                    "Please close them and try again."
                )
                return

            try:
                if self.live_pdf_worker:
                    self.live_pdf_worker.stop()
                    self.live_pdf_worker = None
                if self.on_close_project() is False:
                    return
                shutil.rmtree(path)
                self.log(f"Deleted project: {name}")
                self.update_recent_projects_menu()
            except Exception as e:
                self.log(f"Error deleting project: {e}")
                QMessageBox.critical(self, "Error", f"Failed to delete project:\n{e}")

    def _load_engine_config(self):
        import json
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "engine.json")
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _default_title(self, project_path):
        """First H1 of the extraction (title fallback)."""
        raw_json_path = os.path.join(project_path, "semantic", "raw_semantic.json")
        if os.path.exists(raw_json_path):
            try:
                import json
                from metadata.metadata_resolver import MetadataResolver
                with open(raw_json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return MetadataResolver._find_first_h1(data) or ""
            except Exception:
                pass
        return ""

    def on_document_properties(self):
        info = self.controller.get_current_project_info()
        if not info: return
        
        engine_config = self._load_engine_config()
        default_title = self._default_title(info['path'])

        from gui.metadata_dialog import DocumentPropertiesDialog
        initial_metadata = self.controller.get_project_metadata()
        
        dialog = DocumentPropertiesDialog(self, initial_metadata, default_title, engine_config)
        if dialog.exec():
            new_metadata = dialog.get_metadata()
            try:
                self.controller.save_project_metadata(new_metadata)
            except Exception as e:
                self.log(f"Failed to save document properties: {e}")
                QMessageBox.critical(self, "Error", f"Failed to save document properties:\n{e}")
                return
            if self._is_busy():
                # the running export picks the new values up when it finishes
                self.show_toast("Document properties saved. They will be written to the tagged PDF when the current run finishes.",
                                duration=6000, toast_type="success")
                return
            from gui.struct_tag_editor import find_tagged_output
            if not find_tagged_output(info['path']):
                self.show_toast("Document properties saved. They will be written when the tagged PDF is generated.",
                                duration=5000, toast_type="success")
                return
            if self._apply_document_properties(info['path']):
                self.show_toast("Title and Author updated in the tagged PDF.", duration=4000, toast_type="success")

    def _apply_document_properties(self, project_path: str, quiet: bool = False) -> bool:
        """Write the project's Title / Author into the tagged PDF (Info dictionary +
        XMP), keeping language, PDF/UA identification and DisplayDocTitle."""
        from gui.struct_tag_editor import find_tagged_output
        output_pdf = find_tagged_output(project_path)
        if not output_pdf or not os.path.exists(output_pdf):
            return False
        project_metadata = self.controller.get_project_metadata() or {}
        engine_config = self._load_engine_config()
        try:
            import pikepdf
            from metadata.metadata_model import DocumentMetadata
            from metadata.language_resolver import LanguageResolver
            from metadata.metadata_writer import MetadataWriter

            from engine.file_lock import check_writable
            check_writable(output_pdf, action="apply again")
            tmp = output_pdf + ".meta.tmp.pdf"
            with pikepdf.Pdf.open(output_pdf) as pdf:
                existing_title = str(pdf.docinfo.get("/Title", "") or "")
                meta = DocumentMetadata()
                title_val = str(project_metadata.get("title") or "").strip()
                meta.title = title_val or existing_title or self._default_title(project_path) or \
                    os.path.splitext(os.path.basename(output_pdf))[0]
                existing_lang = str(pdf.Root.get("/Lang", "") or "")
                resolved_lang, _ = LanguageResolver.resolve(
                    project_metadata.get("language"), engine_config.get("language"),
                    existing_lang or engine_config.get("pdfua", {}).get("language") or "en-US")
                meta.language = resolved_lang or "en-US"
                meta.author = str(project_metadata.get("author") or "").strip() or None
                meta.subject = str(project_metadata.get("subject") or "").strip() or None
                meta.keywords = str(project_metadata.get("keywords") or "").strip() or None
                meta.pdfua_part = int(engine_config.get("pdfua", {}).get("part", 1))
                meta.display_doc_title = True

                MetadataWriter.write(pdf, meta)
                if not meta.author and "/Author" in pdf.docinfo:
                    del pdf.docinfo["/Author"]          # Author cleared by the user
                pdf.save(tmp)
            from engine.file_lock import safe_replace
            safe_replace(tmp, output_pdf, action="apply again")
            self.log(f"[INFO] Document properties written to the tagged PDF (Title: {meta.title!r}, Author: {meta.author or '-'}).")
            return True
        except PermissionError as e:
            if not quiet:
                QMessageBox.warning(self, "File in Use" if type(e).__name__ == "FileLockedError"
                                    else "Cannot Write PDF", str(e))
            else:
                self.log(f"[WARN] {e}")
            return False
        except Exception as e:
            self.log(f"Failed to update the tagged PDF's document properties: {e}")
            if not quiet:
                QMessageBox.critical(self, "Error", f"Failed to update the tagged PDF:\n{e}")
            return False

    def _sync_tree_selection(self, el_id, exclude_tree=None):
        if exclude_tree != self.elements_tree:
            self.elements_tree.blockSignals(True)
            iterator = QTreeWidgetItemIterator(self.elements_tree)
            while iterator.value():
                item = iterator.value()
                el = item.data(0, Qt.ItemDataRole.UserRole)
                if el:
                    node_id = el.get('id') or el.get('uuid') or el.get('node_id')
                    if str(node_id) == str(el_id):
                        self.elements_tree.setCurrentItem(item)
                        parent = item.parent()
                        while parent:
                            parent.setExpanded(True)
                            parent = parent.parent()
                        self.elements_tree.scrollToItem(item)
                        break
                iterator += 1
            self.elements_tree.blockSignals(False)

        if exclude_tree != self.tags_tree:
            self.tags_tree.blockSignals(True)
            iterator = QTreeWidgetItemIterator(self.tags_tree)
            while iterator.value():
                item = iterator.value()
                el = item.data(0, Qt.ItemDataRole.UserRole)
                is_text = item.data(0, Qt.UserRole + 1) == "is_text"
                if el and not is_text:
                    node_id = el.get('id') or el.get('uuid') or el.get('node_id')
                    if str(node_id) == str(el_id):
                        self.tags_tree.setCurrentItem(item)
                        parent = item.parent()
                        while parent:
                            parent.setExpanded(True)
                            parent = parent.parent()
                        self.tags_tree.scrollToItem(item)
                        break
                iterator += 1
            self.tags_tree.blockSignals(False)

    def on_canvas_node_selected(self, el_id, p_num):
        self._sync_tree_selection(el_id)
    def _update_undo_redo_actions(self):
        if hasattr(self, 'undo_action'):
            self.undo_action.setEnabled(len(self.action_history.undo_stack) > 0)
        if hasattr(self, 'redo_action'):
            self.redo_action.setEnabled(len(self.action_history.redo_stack) > 0)
        
        is_dirty = self.action_history.is_modified
        if hasattr(self, 'save_proj_action_menu'):
            self.save_proj_action_menu.setEnabled(is_dirty)
        if hasattr(self, 'save_proj_action_tb'):
            self.save_proj_action_tb.setEnabled(is_dirty)
        # (No PDF update here: this runs on every JSON load / save as well. The
        #  edit handlers call schedule_live_pdf_update() themselves.)

    def undo(self):
        if self._is_busy():
            self.show_toast("Please wait - the tagged PDF is being updated.", toast_type="error")
            return
        action = self.action_history.undo(self.all_elements)
        if action and type(action).__name__ == "DeleteStructTagsAction":
            self._update_undo_redo_actions()
            self._undo_struct_delete(action)
            return
        if action:
            self.populate_elements_tree()
            self._update_undo_redo_actions()
            # For structural actions (CreateTagAction), the PDF must be resynchronized
            # from the baseline, not just a lightweight mutation, because lightweight
            # mutation cannot easily strip stream insertions.
            if type(action).__name__ == "CreateTagAction":
                self.trigger_fast_export(list(self.action_history.undo_stack))
            else:
                self.schedule_live_pdf_update()
            if hasattr(action, 'node_id'):
                self.on_canvas_node_selected(action.node_id, 1)
            self.page_viewer.render_page()

    def redo(self):
        if self._is_busy():
            self.show_toast("Please wait - the tagged PDF is being updated.", toast_type="error")
            return
        action = self.action_history.redo(self.all_elements)
        if action and type(action).__name__ == "DeleteStructTagsAction":
            if not self._apply_struct_delete(action):
                self.action_history.undo_stack.remove(action)
                self.action_history.redo_stack.append(action)
            self._update_undo_redo_actions()
            self.populate_tags_tree()
            return
        if action:
            self.populate_elements_tree()
            self._update_undo_redo_actions()
            # Same — structural redo must resynchronize the PDF fully.
            if type(action).__name__ == "CreateTagAction":
                self.trigger_fast_export(list(self.action_history.undo_stack))
            else:
                self.schedule_live_pdf_update()
            if hasattr(action, 'node_id'):
                self.on_canvas_node_selected(action.node_id, 1)
            self.page_viewer.render_page()

    def on_elements_tree_context_menu(self, position):
        self._show_context_menu_for_tree(self.elements_tree, position)

    def on_tag_tree_context_menu(self, position):
        item = self.tags_tree.itemAt(position)
        if not item:
            return
        if not item.isSelected():
            self.tags_tree.setCurrentItem(item)
        tag_item = self._tag_item_for(item)
        menu = QMenu(self)
        if tag_item is not None:
            tag_label = tag_item.text(0) if tag_item.text(0).startswith("<") else "<Document>"
            del_action = menu.addAction(f"Delete Tag  {tag_label}")
            del_action.setShortcut(QKeySequence(Qt.Key_Delete))
            del_action.triggered.connect(lambda checked=False: self.delete_selected_tags())
        # Tags panel: Delete Tag is the only option (undo with Ctrl+Z)
        if not menu.isEmpty():
            menu.exec_(self.tags_tree.viewport().mapToGlobal(position))

    # ------------------------------------------------------------------
    # Delete Tag (Tags panel): removes the tag and all its child tags from the PDF
    # ------------------------------------------------------------------
    def _tag_item_for(self, item):
        """The tag row for `item` (a text row maps to its tag). None if not a tag."""
        cur = item
        while cur is not None:
            if cur.data(0, Qt.UserRole + 3) is not None:
                return cur
            if cur.data(0, Qt.UserRole + 1) == "is_text":
                cur = cur.parent()
                continue
            return None
        return None

    def _is_busy(self):
        if self.worker and self.worker.isRunning():
            return True
        if self.live_pdf_worker and getattr(self.live_pdf_worker, "is_processing", False):
            return True
        return False

    def delete_selected_tags(self):
        info = self.controller.get_current_project_info()
        if not info:
            return
        pdf_path, source = self._tags_source_pdf(info)
        if not pdf_path:
            self.show_toast("This PDF has no tags to delete.", toast_type="error")
            return
        if self._is_busy():
            self.show_toast("Please wait - the tagged PDF is being updated.", toast_type="error")
            return
        items = [self._tag_item_for(it) for it in self.tags_tree.selectedItems()]
        items = [it for it in items if it is not None]
        if not items:
            return
        targets = []
        for it in items:
            path = list(it.data(0, Qt.UserRole + 3) or [])
            targets.append({
                "path": path,
                "tag": it.data(0, Qt.UserRole + 4),
                "node_ref": it.data(0, Qt.UserRole + 5),
                "label": it.text(0),
            })
        # a selected tag inside another selected tag is deleted with its ancestor
        targets.sort(key=lambda t: len(t["path"]))
        kept = []
        for t in targets:
            if not any(t["path"][:len(k["path"])] == k["path"] for k in kept):
                kept.append(t)
        targets = kept
        if any(not t["path"] or t["tag"] == "/Document" for t in targets):
            reply = QMessageBox.question(
                self, "Delete All Tags",
                "Deleting <Document> removes every tag in this PDF.\n\nContinue?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
        from gui.edit_actions import DeleteStructTagsAction
        action = DeleteStructTagsAction(targets, pdf_path=pdf_path, source=source)
        if not self._apply_struct_delete(action):
            return
        self.action_history.execute(action, self.all_elements)
        self._update_undo_redo_actions()
        self.populate_tags_tree()

    def _tags_source_pdf(self, info):
        """The PDF whose tags the Tags panel shows: TagPilot's tagged output, or -
        before the first pipeline run - the PDF itself when it is already tagged.
        Returns (path, "output" | "input") or (None, None)."""
        from gui.struct_tag_editor import find_tagged_output
        if not info:
            return None, None
        out = find_tagged_output(info['path'])
        if out and os.path.exists(out):
            return out, "output"
        src = info.get('input_pdf')
        if src and os.path.exists(src):
            try:
                import pikepdf
                with pikepdf.Pdf.open(src) as p:
                    if "/StructTreeRoot" in p.Root:
                        return src, "input"
            except Exception:
                pass
        return None, None

    @staticmethod
    def _make_project_copy_writable(path):
        """The project's own copy of the PDF may have inherited a read-only
        attribute from the user's original file; TagPilot may write its copy."""
        try:
            import stat
            mode = os.stat(path).st_mode
            if not (mode & stat.S_IWRITE):
                os.chmod(path, mode | stat.S_IWRITE)
        except OSError:
            pass

    def _apply_struct_delete(self, action, quiet=False):
        """Run the deletion on the tagged PDF. Keeps a snapshot for exact undo."""
        from gui.struct_tag_editor import delete_tags_in_file, find_tagged_output
        info = self.controller.get_current_project_info()
        if getattr(action, "source", "output") == "input":
            # existing tags of the original PDF (no tagged output yet)
            pdf_path = action.pdf_path or (info.get('input_pdf') if info else None)
        else:
            pdf_path = find_tagged_output(info['path']) if info else action.pdf_path
        if not pdf_path or not os.path.exists(pdf_path):
            return False
        if getattr(action, "source", "output") == "input":
            self._make_project_copy_writable(pdf_path)
        try:
            with open(pdf_path, "rb") as f:
                snapshot = f.read()
            stats = delete_tags_in_file(pdf_path, action.targets,
                                        renumber_notes=getattr(action, "source", "output") != "input")
        except PermissionError as e:
            if not quiet:
                QMessageBox.warning(self, "File in Use" if type(e).__name__ == "FileLockedError"
                                    else "Cannot Write PDF", str(e))
            else:
                self.log(f"[WARN] {e}")
            return False
        except Exception as e:
            if quiet:
                self.log(f"[WARN] Could not re-apply tag deletion ({action.label}): {e}")
            else:
                QMessageBox.warning(self, "Delete Tag", f"Could not delete the tag:\n{e}")
            return False
        action.snapshot = snapshot
        action.pdf_path = pdf_path
        msg = f"Deleted {action.label}: {stats['elements']} tag(s) removed."
        self.log(f"[INFO] {msg}")
        if not quiet:
            self.statusBar.showMessage(msg, 6000)
        return True

    def _undo_struct_delete(self, action):
        """Exact undo from the snapshot; without one (after reopening the project)
        the tagged PDF is rebuilt from the JSON and the remaining deletions."""
        if action.snapshot and action.pdf_path and os.path.exists(os.path.dirname(action.pdf_path)):
            try:
                from engine.file_lock import check_writable, safe_replace
                if getattr(action, "source", "output") == "input":
                    self._make_project_copy_writable(action.pdf_path)
                check_writable(action.pdf_path, action="undo again")
                tmp = action.pdf_path + ".undo.tmp.pdf"
                with open(tmp, "wb") as f:
                    f.write(action.snapshot)
                safe_replace(tmp, action.pdf_path, action="undo again")
                self.populate_tags_tree()
                return
            except PermissionError as e:
                QMessageBox.warning(self, "File in Use" if type(e).__name__ == "FileLockedError" else "Cannot Undo", str(e))
                self.action_history.redo_stack.remove(action) if action in self.action_history.redo_stack else None
                self.action_history.undo_stack.append(action)
                self._update_undo_redo_actions()
                return
            except Exception as e:
                self.log(f"[WARN] Snapshot restore failed: {e}")
        if getattr(action, "source", "output") == "input":
            # the original PDF's own tags cannot be rebuilt from the JSON
            self.show_toast("This deletion of the PDF's original tags can only be undone in the session "
                            "where it was made.", toast_type="warning")
            return
        self.trigger_fast_export(list(self.action_history.undo_stack))

    def _reapply_struct_deletions(self, actions):
        """After the tagged PDF was regenerated, delete the same tags again."""
        for action in actions:
            action.snapshot = None
            self._apply_struct_delete(action, quiet=True)

    def _show_context_menu_for_tree(self, tree, position):
        item = tree.itemAt(position)
        if not item:
            return
            
        elements_to_delete = []
        def gather(node):
            node_el = node.data(0, Qt.ItemDataRole.UserRole)
            if node_el:
                elements_to_delete.append(node_el)
            for i in range(node.childCount()):
                gather(node.child(i))
        gather(item)

        el = item.data(0, Qt.ItemDataRole.UserRole)
        el_id = el.get('id') or el.get('uuid') or el.get('node_id') if el else None
        
        if not el or not el_id:
            # It's a structural node, or something without a direct element mapping
            if elements_to_delete:
                menu = QMenu(self)
                remove_action = menu.addAction(f"Delete Node & Children ({len(elements_to_delete)} elements)")
                remove_action.triggered.connect(lambda checked, els=elements_to_delete: self._delete_multiple_elements(els))
                menu.exec_(tree.viewport().mapToGlobal(position))
            return
            
        self._show_context_menu_from_item(item, el, tree.viewport().mapToGlobal(position), elements_to_delete)

    def _show_context_menu_from_item(self, item, el, global_pos, elements_to_delete=None):
        menu = QMenu(self)
        roles = ["Heading", "Paragraph", "List", "Table", "Figure", "Caption", "Form", "FormLabel", "Note", "Formula", "TableOfContents", "Header", "Footer", "PageNumber", "Artifact"]
        change_role_menu = menu.addMenu("Change Role")
        for role in roles:
            action = change_role_menu.addAction(role)
            action.setData(role)
            action.triggered.connect(lambda checked, r=role, it=item, e=el: self._change_role(it, e, r))
            
        menu.addSeparator()
        if elements_to_delete and len(elements_to_delete) > 1:
            remove_action = menu.addAction(f"Remove Element & Children ({len(elements_to_delete)} elements)")
            remove_action.triggered.connect(lambda checked, els=elements_to_delete: self._delete_multiple_elements(els))
        else:
            remove_action = menu.addAction("Remove Element")
            remove_action.triggered.connect(lambda checked, it=item, e=el: self._delete_element(it, e))

        menu.exec_(global_pos)

    def _change_role(self, item, el, new_role):
        node_id = el.get('id') or el.get('uuid') or el.get('node_id')
        if not node_id:
            return
            
        old_role = el.get('role') or el.get('semantic', {}).get('type') or el.get('semantic_type') or el.get('type')
        action = UpdateRoleAction(node_id, old_role, new_role)
        if self.action_history.execute(action, self.all_elements):
            self.populate_elements_tree()
            self._update_undo_redo_actions()
            self.schedule_live_pdf_update()
            
            p_num = el.get('metadata', {}).get('page') or el.get('page_number', 1)
            self.on_canvas_node_selected(node_id, p_num)


    def on_canvas_node_right_clicked(self, el_id, p_num, global_pos):
        self.on_canvas_node_selected(el_id, p_num)
        
        # Find the selected item to show context menu
        iterator = QTreeWidgetItemIterator(self.elements_tree)
        target_item = None
        target_el = None
        while iterator.value():
            item = iterator.value()
            el = item.data(0, Qt.ItemDataRole.UserRole)
            if el:
                node_id = el.get('id') or el.get('uuid') or el.get('node_id')
                if node_id == el_id:
                    target_item = item
                    target_el = el
                    break
            iterator += 1
            
        if target_item and target_el:
            self._show_context_menu_from_item(target_item, target_el, global_pos)



    def _delete_element(self, item, el):
        from gui.edit_actions import RemoveTagAction
        
        node_id = el.get('id') or el.get('uuid') or el.get('node_id')
        if not node_id:
            return
            
        action = RemoveTagAction(node_id, el)
        if self.action_history.execute(action, self.all_elements):
            self.populate_elements_tree()
            self._update_undo_redo_actions()
            if hasattr(self, 'page_viewer'):
                self.page_viewer.load_elements(self.all_elements)
                self.page_viewer.render_page()
            self.schedule_live_pdf_update()

    def _delete_multiple_elements(self, els):
        if not els:
            return
        from gui.edit_actions import BatchRemoveTagAction
        action = BatchRemoveTagAction(els)
        if self.action_history.execute(action, self.all_elements):
            self.populate_elements_tree()
            self._update_undo_redo_actions()
            if hasattr(self, 'page_viewer'):
                self.page_viewer.load_elements(self.all_elements)
                self.page_viewer.render_page()
            self.schedule_live_pdf_update()

    def delete_selected_element(self, from_backspace=False):
        elements_to_delete = []
        if self.tags_tree.hasFocus():
            # Tags panel: the Delete key deletes the selected tag (and its children)
            if not from_backspace and self.tags_tree.selectedItems():
                self.delete_selected_tags()
            return
                    
        if not elements_to_delete and hasattr(self, 'page_viewer') and hasattr(self.page_viewer, 'selected_element_ids') and self.page_viewer.selected_element_ids:
            for el_id in self.page_viewer.selected_element_ids:
                for el in self.all_elements:
                    if str(el.get('id')) == str(el_id) or str(el.get('node_id')) == str(el_id) or str(el.get('uuid')) == str(el_id):
                        elements_to_delete.append(el)
                        break
                        
        if elements_to_delete:
            self._delete_multiple_elements(elements_to_delete)

    def on_save_project(self):
        info = self.controller.get_current_project_info()
        if not info:
            return
            
        try:
            PersistenceManager.save_edits(info['path'], self.action_history.undo_stack)
            self.action_history.save_checkpoint()
            self._update_undo_redo_actions()
            self.log("Manual edits saved successfully.")
            return True
        except Exception as e:
            self.log(f"Failed to save manual edits: {e}")
            QMessageBox.critical(self, "Save Error", f"Failed to save manual edits:\n{e}")
            return False

    def check_dirty_state(self) -> bool:
        """Returns False if user cancels, True if they save/discard or project is clean."""
        if not self.action_history.is_modified:
            return True
            
        reply = QMessageBox.question(
            self, 'Unsaved Changes',
            "You have unsaved manual edits. Do you want to save them?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save
        )
        
        if reply == QMessageBox.Save:
            return self.on_save_project()
        elif reply == QMessageBox.Discard:
            return True
        return False

    def closeEvent(self, event):
        if not self._force_quit and self.worker and self.worker.isRunning():
            reply = QMessageBox.question(
                self, "Pipeline Running",
                "A pipeline is still running. Stop it and exit TagPilot?\n(Completed pages are cached.)",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                event.ignore()
                return
        if not self._force_quit and not self.check_dirty_state():
            event.ignore()
            return
        for t in ("connection_timer", "license_timer", "pipeline_timer"):
            if hasattr(self, t):
                getattr(self, t).stop()
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.statusBar.showMessage("Stopping pipeline...")
            QApplication.processEvents()
            self.worker.wait(20000)
        if self.live_pdf_worker:
            self.live_pdf_worker.stop()
        event.accept()

    def schedule_live_pdf_update(self):
        """Schedule a lightweight background PDF tag update."""
        info = self.controller.get_current_project_info()
        if not info:
            return
            
        if self.worker and self.worker.isRunning():
            # Pipeline is already running, avoid conflict
            return
            
        if not self.live_pdf_worker or not self.live_pdf_worker.isRunning():
            from gui.live_pdf_editor import LivePdfEditorWorker
            profile = info.get("profile") or {}
            document_type = self._effective_doc_type(profile)
            config = {"document_type": document_type, "project_metadata": self.controller.get_project_metadata()}
            
            self.live_pdf_worker = LivePdfEditorWorker(info['path'], info['input_pdf'], config, self)
            self.live_pdf_worker.progress_signal.connect(lambda msg, pct: self.on_progress(pct, 100, msg))
            self.live_pdf_worker.finished_signal.connect(self.on_live_update_finished)
            self.live_pdf_worker.trigger_fast_export_signal.connect(self.trigger_fast_export)
            self.live_pdf_worker.start()
            
        active_edits = list(self.action_history.undo_stack)
        self.live_pdf_worker.schedule_update(active_edits)

    def trigger_fast_export(self, active_edits):
        """Trigger the fast-path PipelineWorker for complex structural edits (e.g., Artifacts)."""
        pass # Removed wait cursor
        info = self.controller.get_current_project_info()
        if not info:
            pass # Removed restore cursor
            return
            
        if self.worker and self.worker.isRunning():
            pass # Removed restore cursor
            return
            
        # raw_semantic.json is the immutable AI extraction: never overwritten here.
        # The exporter rebuilds from graph/rules_v1.json + the edit history.
        self._pending_struct_deletes = [a for a in self.action_history.undo_stack
                                        if type(a).__name__ == "DeleteStructTagsAction"
                                        and getattr(a, "source", "output") != "input"]
        from app.workers import PipelineWorker
        profile = info.get("profile") or {}
        document_type = self._effective_doc_type(profile)
        config = {"document_type": document_type, "project_metadata": self.controller.get_project_metadata()}
        
        active_edits = [a for a in active_edits if type(a).__name__ != "DeleteStructTagsAction"]
        self.worker = PipelineWorker(info['path'], info['input_pdf'], config, active_edits)
        self.worker.finished_signal.connect(self.on_fast_export_finished)
        self.worker.progress_signal.connect(self.on_progress)
        self.worker.start()

    def on_canvas_bbox_modified(self, el_id: str, new_bbox: list, page_number: int):
        try:
            from gui.edit_actions import UpdateBBoxAction
            
            info = self.controller.get_current_project_info()
            if not info or not hasattr(self, 'all_elements') or not self.all_elements:
                return

            # Find old bbox
            old_bbox = None
            for el in self.all_elements:
                if str(el.get('id')) == str(el_id) or str(el.get('node_id')) == str(el_id) or str(el.get('uuid')) == str(el_id):
                    old_bbox = el.get('geometry', {}).get('bbox') or el.get('bounding_box')
                    break
                    
            if not old_bbox:
                old_bbox = new_bbox

            action = UpdateBBoxAction(el_id, old_bbox, new_bbox)
            if self.action_history.execute(action, self.all_elements):
                self.populate_elements_tree()
                self._update_undo_redo_actions()
                self.schedule_live_pdf_update()
                
                # Re-select the element to redraw the newly updated box on the page viewer
                self.on_canvas_node_selected(el_id, page_number)
            else:
                self.log(f"[Warning] Failed to edit element {el_id}. It may not exist in the current pipeline stage.")
                self.page_viewer.render_page()

        except Exception as e:
            import traceback
            print(f"[ERROR] Exception in on_canvas_bbox_modified: {e}")
            traceback.print_exc()

    def on_tag_region_selected(self, bbox: list, page_number: int, role_hint: str):
        """
        Handle a new tag selection from the UI.
        
        The Tag Content tool stays ON after this call regardless of outcome.
        """
        from gui.edit_actions import CreateTagAction
        import uuid

        # Ensure we're in a valid project
        info = self.controller.get_current_project_info()
        if not info:
            self.on_tag_content_toggled(False)
            if hasattr(self, 'tag_content_action'):
                self.tag_content_action.setChecked(False)
            return

        bx0, by0, bx1, by1 = bbox

        # -- Role determination --
        if role_hint is not None:
            role = role_hint
        else:
            # Open Acrobat-style role dialog
            dialog = RoleSelectionDialog(self)
            if dialog.exec() != QDialog.Accepted or not dialog.selected_role:
                self.statusBar.showMessage("Tag creation cancelled. Draw another rectangle.")
                return  # Tag Content mode stays ON
            role = dialog.selected_role

        # -- Early bbox overlap check against existing DocumentGraph elements
        for el in self.all_elements:
            el_page = el.get('metadata', {}).get('page') or el.get('page_number', 1)
            if el_page != page_number:
                continue
            el_bbox = (el.get('geometry') or {}).get('bbox') or el.get('bounding_box') or el.get('bbox', [])
            if len(el_bbox) == 4:
                ex0, ey0, ex1, ey1 = el_bbox
                overlap = not (bx1 < ex0 or bx0 > ex1 or by1 < ey0 or by0 > ey1)
                if overlap:
                    QMessageBox.warning(
                        self, "Already Tagged",
                        "The selected region overlaps an existing tagged element. "
                        "Cannot create a new tag on already-tagged content."
                    )
                    self.statusBar.showMessage("Tag creation rejected: overlaps existing tag.")
                    return  # Tag Content mode stays ON

        # -- Extract content text from selection using PyMuPDF
        content_text = ""
        try:
            import pymupdf
            fitz_doc = pymupdf.open(info['input_pdf'])
            if page_number - 1 < len(fitz_doc):
                fitz_page = fitz_doc[page_number - 1]
                page_h = fitz_page.rect.height
                fitz_rect = pymupdf.Rect(bx0, page_h - by1, bx1, page_h - by0)
                words = fitz_page.get_text("words", clip=fitz_rect)
                content_text = " ".join(w[4] for w in words)
            fitz_doc.close()
        except Exception as e:
            self.log(f"[Tag Content] Content extraction failed (non-fatal): {e}")

        # -- Determine reading_order
        page_orders = []
        for el in self.all_elements:
            el_page = el.get('metadata', {}).get('page') or el.get('page_number', 1)
            if el_page == page_number:
                ro = el.get('reading_order') or el.get('id') or 0
                try:
                    page_orders.append(int(ro))
                except (TypeError, ValueError):
                    pass
        reading_order = (max(page_orders) + 10) if page_orders else 10

        # -- Build a DocumentGraph-compatible node dict
        new_uuid = str(uuid.uuid4())

        node_dict = {
            "id": reading_order,
            "uuid": new_uuid,
            "text": content_text,
            "semantic": {
                "type": role,
                "confidence": 1.0,
                "pdf_tag": None,
                "source": "manual_tag"
            },
            "visual": {"widgets": []},
            "geometry": {
                "bbox": bbox,
                "width": bbox[2] - bbox[0],
                "height": bbox[3] - bbox[1],
                "center": [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2],
                "area": (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]),
                "relative_x": None, "relative_y": None,
                "row": None, "column": None, "alignment": None
            },
            "relations": {
                "parent": None, "children": [],
                "previous": None, "next": None,
                "table": None, "caption_of": None, "neighbors": {}
            },
            "metadata": {"page": page_number},
            "analysis": {"notes": []},
            "status": {
                "normalized": True,
                "geometry_analyzed": True,
                "visual_analyzed": False,
                "semantic_detected": True,
                "semantic_analyzed": True,
                "consistency_analyzed": False,
                "qa_passed": False,
                "validated": False
            },
            "history": [f"Manual Tag: Created as {role} via Tag Content tool"]
        }

        # -- Push to ActionHistory
        action = CreateTagAction(new_uuid, node_dict)
        if self.action_history.execute(action, self.all_elements):
            if hasattr(self.page_viewer.view, '_clear_active_selection'):
                self.page_viewer.view._clear_active_selection()
            self.populate_elements_tree()
            self._update_undo_redo_actions()
            self.statusBar.showMessage(
                f"Tag '{role}' created: '{content_text[:50]}' — draw next rectangle or ESC to exit."
            )
            self.log(f"[Tag Content] Created {role} tag (uuid={new_uuid}, page={page_number})")
            self.schedule_live_pdf_update()
            if hasattr(self.page_viewer.view, 'setFocus'):
                self.page_viewer.view.setFocus()
        else:
            self.statusBar.showMessage("Tag creation failed (node already exists).")
        # Tag Content mode stays ON — no setChecked(False) or cursor reset here.
