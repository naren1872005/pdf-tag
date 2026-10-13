import fitz  # PyMuPDF
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
    QLabel, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QToolBar, QLineEdit
)
from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QImage, QPixmap, QPainter, QAction, QPen, QColor, QBrush
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsItem
from PySide6.QtCore import Qt, QRectF, Signal, QPointF
from PySide6.QtGui import QPainterPath

class DraggableBoundingBox(QGraphicsRectItem):
    def __init__(self, rect, el_id, pen, brush, viewer):
        super().__init__(rect)
        self.el_id = el_id
        self.viewer = viewer
        self.setPen(pen)
        # Use the provided brush (which is semi-transparent) so the center is clickable.
        # If the user hates the tint, we can change the alpha, but we need the center to be solid for dragging.
        # Wait, if we use a transparent brush, it's clickable but invisible!
        brush.setColor(QColor(brush.color().red(), brush.color().green(), brush.color().blue(), 1)) # almost invisible, but clickable
        self.setBrush(brush)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setAcceptHoverEvents(True)
        self._is_resizing = False
        self._resize_edges = []

    def _get_resize_edges(self, pos):
        rect = self.rect()
        margin = 15
        edges = []
        if pos.x() - rect.left() <= margin: edges.append('left')
        elif rect.right() - pos.x() <= margin: edges.append('right')
        if pos.y() - rect.top() <= margin: edges.append('top')
        elif rect.bottom() - pos.y() <= margin: edges.append('bottom')
        return edges

    def hoverMoveEvent(self, event):
        edges = self._get_resize_edges(event.pos())
        if 'left' in edges and 'top' in edges: self.setCursor(Qt.SizeFDiagCursor)
        elif 'right' in edges and 'bottom' in edges: self.setCursor(Qt.SizeFDiagCursor)
        elif 'left' in edges and 'bottom' in edges: self.setCursor(Qt.SizeBDiagCursor)
        elif 'right' in edges and 'top' in edges: self.setCursor(Qt.SizeBDiagCursor)
        elif 'left' in edges or 'right' in edges: self.setCursor(Qt.SizeHorCursor)
        elif 'top' in edges or 'bottom' in edges: self.setCursor(Qt.SizeVerCursor)
        else: self.setCursor(Qt.OpenHandCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event):
        edges = self._get_resize_edges(event.pos())
        # Only resize if clicking a corner, otherwise drag!
        is_corner = len(edges) >= 2
        if event.button() == Qt.LeftButton and is_corner:
            self._is_resizing = True
            self._resize_edges = edges
            event.accept()
        else:
            # Let QGraphicsRectItem handle the drag
            self.setCursor(Qt.ClosedHandCursor)
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._is_resizing:
            new_rect = self.rect()
            pos = event.pos()
            if 'left' in self._resize_edges: new_rect.setLeft(min(pos.x(), new_rect.right() - 5))
            if 'right' in self._resize_edges: new_rect.setRight(max(pos.x(), new_rect.left() + 5))
            if 'top' in self._resize_edges: new_rect.setTop(min(pos.y(), new_rect.bottom() - 5))
            if 'bottom' in self._resize_edges: new_rect.setBottom(max(pos.y(), new_rect.top() + 5))
            self.setRect(new_rect)
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        from PySide6.QtCore import QTimer
        self.setCursor(Qt.OpenHandCursor)
        viewer = self.viewer
        el_id = self.el_id
        
        def safe_drag_callback(v, e, r):
            try:
                v.on_bbox_dragged(e, r)
            except Exception as ex:
                import traceback
                print(f"[ERROR] Exception in on_bbox_dragged: {ex}")
                traceback.print_exc()

        if self._is_resizing:
            self._is_resizing = False
            rect = self.mapRectToScene(self.rect())
            QTimer.singleShot(0, lambda v=viewer, e=el_id, r=rect: safe_drag_callback(v, e, r))
        else:
            super().mouseReleaseEvent(event)
            rect = self.mapRectToScene(self.rect())
            QTimer.singleShot(0, lambda v=viewer, e=el_id, r=rect: safe_drag_callback(v, e, r))

class FloatingRolePalette(QWidget):
    role_selected_signal = Signal(str)
    more_requested_signal = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.NoFocus)
        self.setStyleSheet("""
            FloatingRolePalette {
                background-color: #252526;
                border: 1px solid #555555;
                border-radius: 4px;
            }
            QPushButton {
                background: transparent;
                color: #dcdcdc;
                border: none;
                padding: 6px 10px;
                font-family: Consolas, monospace;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #3d3d3d;
                border-radius: 2px;
            }
            QPushButton:pressed {
                background-color: #0e639c;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        roles = [
            ("P", "Paragraph"), ("H1", "Heading 1"), ("H2", "Heading 2"), 
            ("H3", "Heading 3"), ("Lbl", "List Label"), ("LBody", "List Body"),
            ("TD", "Table Data"), ("TH", "Table Header")
        ]
        for role_code, tooltip in roles:
            btn = QPushButton(role_code)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setToolTip(f"{tooltip}")
            btn.clicked.connect(lambda checked=False, r=role_code: self.role_selected_signal.emit(r))
            layout.addWidget(btn)

        # Separator line
        sep = QWidget()
        sep.setFixedWidth(1)
        sep.setStyleSheet("background-color: #555555;")
        layout.addWidget(sep)

        more_btn = QPushButton("More...")
        more_btn.setFocusPolicy(Qt.NoFocus)
        more_btn.setToolTip("Open role selection dialog")
        more_btn.clicked.connect(lambda: self.more_requested_signal.emit())
        layout.addWidget(more_btn)

        self.hide()

class PDFGraphicsView(QGraphicsView):
    """
    PDF canvas with persistent Acrobat-like Tag Content mode.

    Tagging Mode ON:
      - Cursor: CrossCursor
      - Mouse drag draws a selection rectangle.
      - Rectangle stays visible (active_selection_rect) after mouse release.
      - Keyboard shortcuts commit the tag without a dialog:
          P          -> Text/Paragraph
          H then 1-6 -> Heading 1-6
          Enter      -> open role-selection dialog
          ESC        -> cancel selection / exit tagging mode
      - Mode stays ON until the user presses ESC or un-checks Tag Content.

    Tagging Mode OFF:
      - Normal ScrollHandDrag (pan) behaviour.
      - Cursor restored to Qt.OpenHandCursor (Qt's scroll-hand default).
    """

    def __init__(self, scene, viewer_parent):
        super().__init__(scene)
        self.viewer_parent = viewer_parent
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAlignment(Qt.AlignHCenter | Qt.AlignTop)

        # ---- Tagging state -------------------------------------------------
        self.drag_start = None            # QPointF scene pos of press
        self.drag_rect = None             # live rubber-band rect item
        self.active_selection_rect = None # QGraphicsRectItem kept after release
        self.active_selection_scene_rect = None  # QRectF in scene coords
        self._pending_h = False           # True while waiting for digit after H

        self.floating_palette = FloatingRolePalette(self)
        self.floating_palette.role_selected_signal.connect(self._commit_tag_with_role)
        self.floating_palette.more_requested_signal.connect(self._open_role_dialog)

    # ------------------------------------------------------------------
    # Palette Positioning
    # ------------------------------------------------------------------
    def _update_palette_position(self):
        if not self.active_selection_scene_rect or not self.floating_palette.isVisible():
            return
            
        # Convert scene rect to viewport coords
        view_rect = self.mapFromScene(self.active_selection_scene_rect).boundingRect()
        
        # Position palette just above the selection
        palette_size = self.floating_palette.sizeHint()
        px = view_rect.left()
        py = view_rect.top() - palette_size.height() - 4
        
        # If insufficient room above, place it below
        if py < 0:
            py = view_rect.bottom() + 4
            
        # Clamp horizontally to viewport
        vp_width = self.viewport().width()
        if px + palette_size.width() > vp_width:
            px = vp_width - palette_size.width() - 4
        if px < 0:
            px = 4
            
        self.floating_palette.move(px, py)
        self.floating_palette.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._in_tagging_mode() and self.active_selection_rect:
            self._update_palette_position()
            
    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        if self._in_tagging_mode() and self.active_selection_rect:
            self._update_palette_position()

    # ------------------------------------------------------------------
    # Mode helpers
    # ------------------------------------------------------------------
    def _in_tagging_mode(self):
        return getattr(self.viewer_parent, 'tagging_mode', False)

    def _clear_active_selection(self):
        """Remove the highlighted selection rectangle from the scene."""
        if self.active_selection_rect is not None:
            try:
                self.scene().removeItem(self.active_selection_rect)
            except Exception:
                pass
            self.active_selection_rect = None
        self.active_selection_scene_rect = None
        self._pending_h = False
        self.floating_palette.hide()

    def _clear_rubber_band(self):
        """Remove the in-progress drag rectangle."""
        if self.drag_rect is not None:
            try:
                self.scene().removeItem(self.drag_rect)
            except Exception:
                pass
            self.drag_rect = None
        self.drag_start = None

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self._in_tagging_mode():
                # If there is already an active selection, clear it first
                self._clear_active_selection()
                scene_pos = self.mapToScene(event.pos())
                self.drag_start = scene_pos
                self.drag_rect = QGraphicsRectItem()
                self.drag_rect.setPen(QPen(QColor(30, 180, 80), 2, Qt.DashLine))
                self.drag_rect.setBrush(QBrush(QColor(30, 180, 80, 40)))
                self.scene().addItem(self.drag_rect)
                event.accept()
                return
            else:
                scene_pos = self.mapToScene(event.pos())
                self.viewer_parent.on_canvas_clicked(scene_pos)
        elif event.button() == Qt.RightButton:
            scene_pos = self.mapToScene(event.pos())
            self.viewer_parent.on_canvas_right_clicked(scene_pos, event.globalPos())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._in_tagging_mode() and self.drag_start is not None and self.drag_rect is not None:
            scene_pos = self.mapToScene(event.pos())
            rect = QRectF(self.drag_start, scene_pos).normalized()
            self.drag_rect.setRect(rect)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if (event.button() == Qt.LeftButton
                and self._in_tagging_mode()
                and self.drag_start is not None
                and self.drag_rect is not None):
            scene_pos = self.mapToScene(event.pos())
            rect = QRectF(self.drag_start, scene_pos).normalized()
            # Remove the green rubber-band rect
            self._clear_rubber_band()
            if rect.width() > 10 and rect.height() > 10:
                # Keep the selection highlighted in a brighter solid colour
                sel_item = QGraphicsRectItem()
                sel_item.setRect(rect)
                sel_item.setPen(QPen(QColor(255, 160, 0), 2, Qt.SolidLine))
                sel_item.setBrush(QBrush(QColor(255, 160, 0, 40)))
                self.scene().addItem(sel_item)
                self.active_selection_rect = sel_item
                self.active_selection_scene_rect = rect
                self._pending_h = False
                
                # Show floating palette
                self.floating_palette.show()
                self._update_palette_position()

                # Give ourselves keyboard focus so shortcuts work immediately
                self.setFocus()
                self.viewer_parent.show_status(
                    "Selection ready — press P / H+1-6 to tag, Enter for dialog, ESC to cancel"
                )
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    # Wheel / zoom (unchanged)
    # ------------------------------------------------------------------
    def wheelEvent(self, event):
        if event.modifiers() == Qt.ControlModifier:
            if event.angleDelta().y() > 0:
                self.viewer_parent.zoom_in()
            else:
                self.viewer_parent.zoom_out()
            if self._in_tagging_mode() and self.active_selection_rect:
                self._update_palette_position()
            event.accept()
        else:
            super().wheelEvent(event)

    # ------------------------------------------------------------------
    # Keyboard event handling
    # ------------------------------------------------------------------
    def keyPressEvent(self, event):
        # Always handle zoom shortcuts regardless of mode
        if event.modifiers() == Qt.ControlModifier:
            if event.key() in (Qt.Key_Plus, Qt.Key_Equal):
                self.viewer_parent.zoom_in()
                event.accept()
                return
            elif event.key() == Qt.Key_Minus:
                self.viewer_parent.zoom_out()
                event.accept()
                return

        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            if self.viewer_parent.selected_element_id:
                if hasattr(self.viewer_parent, 'main_window'):
                    self.viewer_parent.main_window.delete_selected_element()
                event.accept()
                return

        # Only intercept tagging shortcuts when mode is ON
        if self._in_tagging_mode():
            key = event.key()

            # ESC — cancel selection or exit tagging mode
            if key == Qt.Key_Escape:
                self._pending_h = False
                if self.active_selection_rect is not None:
                    self._clear_active_selection()
                    self.viewer_parent.show_status("Selection cancelled. Draw another rectangle.")
                else:
                    # Exit tagging mode entirely
                    self.viewer_parent.on_tag_content_toggled(False)
                    if hasattr(self.viewer_parent, 'tag_content_action'):
                        self.viewer_parent.tag_content_action.setChecked(False)
                event.accept()
                return

            # Only process shortcut keys if there is an active selection
            if self.active_selection_rect is not None:
                # --- P shortcut: Text/Paragraph ---
                if key == Qt.Key_P and not self._pending_h:
                    self._pending_h = False
                    self._commit_tag_with_role('P')
                    event.accept()
                    return

                # --- H: enter pending-H state ---
                if key == Qt.Key_H and not self._pending_h:
                    self._pending_h = True
                    self.viewer_parent.show_status("H pressed — press 1-6 to select heading level")
                    event.accept()
                    return

                # --- Digit after H ---
                if self._pending_h:
                    digit_map = {
                        Qt.Key_1: 'H1', Qt.Key_2: 'H2', Qt.Key_3: 'H3',
                        Qt.Key_4: 'H4', Qt.Key_5: 'H5', Qt.Key_6: 'H6',
                    }
                    if key in digit_map:
                        role = digit_map[key]
                        self._pending_h = False
                        self._commit_tag_with_role(role)
                        event.accept()
                        return
                    else:
                        # Invalid key after H — clear pending state
                        self._pending_h = False
                        self.viewer_parent.show_status(
                            "Invalid key after H. Press P / H+1-6 / Enter / ESC."
                        )
                        event.accept()
                        return

                # --- Enter/Return: open role selection dialog ---
                if key in (Qt.Key_Return, Qt.Key_Enter):
                    self._pending_h = False
                    self._open_role_dialog()
                    event.accept()
                    return

        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Tag commitment helpers
    # ------------------------------------------------------------------
    def _commit_tag_with_role(self, role: str):
        """Commit the active selection as a tag with the given role."""
        if not self.active_selection_scene_rect: return
        rect = self.active_selection_scene_rect
        self.viewer_parent.on_tag_region_selected(rect, role)
        self.setFocus()

    def _open_role_dialog(self):
        """Open the full Acrobat-style role selection dialog for the active selection."""
        if not self.active_selection_scene_rect: return
        rect = self.active_selection_scene_rect
        self.viewer_parent.on_tag_region_selected(rect, None)
        self.setFocus()

class PageViewer(QWidget):
    node_selected_signal = Signal(object, int) # element_id, page_number
    node_right_clicked_signal = Signal(object, int, object) # element_id, page_number, global_pos
    tag_region_selected_signal = Signal(list, int, object)  # bbox, page_number, role_hint
    bbox_modified_signal = Signal(object, list, int) # element_id, new_bbox, page_number


    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc = None
        self.current_page_idx = 0
        self.zoom_level = 1.0
        self.elements = [] # Semantic JSON elements
        self.selected_element_id = None
        self.tagging_mode = False
        self._main_window = None  # set by MainWindow after construction

        
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.toolbar = QToolBar()
        self.toolbar.setStyleSheet("background-color: #252526; border-bottom: 1px solid #3d3d3d;")
        layout.addWidget(self.toolbar)
        
        self.btn_prev = QAction(" ▲ ", self)
        self.btn_prev.triggered.connect(self.prev_page)
        self.toolbar.addAction(self.btn_prev)
        
        self.btn_next = QAction(" ▼ ", self)
        self.btn_next.triggered.connect(self.next_page)
        self.toolbar.addAction(self.btn_next)
        
        self.page_input = QLineEdit()
        self.page_input.setFixedWidth(40)
        self.page_input.setAlignment(Qt.AlignCenter)
        self.page_input.setStyleSheet("background-color: #3d3d3d; color: #dcdcdc; border: 1px solid #555; border-radius: 2px;")
        self.page_input.returnPressed.connect(self.goto_page_input)
        self.toolbar.addWidget(self.page_input)
        
        self.lbl_page_total = QLabel(" / 0 ")
        self.lbl_page_total.setStyleSheet("color: #dcdcdc; font-weight: bold; padding: 0 5px;")
        self.toolbar.addWidget(self.lbl_page_total)
        
        self.toolbar.addSeparator()
        
        self.btn_zoom_out = QAction("➖ Zoom Out", self)
        self.btn_zoom_out.triggered.connect(self.zoom_out)
        self.toolbar.addAction(self.btn_zoom_out)
        
        self.lbl_zoom = QLabel(" 100% ")
        self.lbl_zoom.setStyleSheet("color: #dcdcdc; padding: 0 10px;")
        self.toolbar.addWidget(self.lbl_zoom)
        
        self.btn_zoom_in = QAction("➕ Zoom In", self)
        self.btn_zoom_in.triggered.connect(self.zoom_in)
        self.toolbar.addAction(self.btn_zoom_in)
        
        self.scene = QGraphicsScene()
        self.view = PDFGraphicsView(self.scene, self)
        self.view.setStyleSheet("background-color: #1e1e1e; border: none;")
        self.view.verticalScrollBar().valueChanged.connect(self._on_scroll)
        layout.addWidget(self.view)
        
        self.update_controls()

    def load_pdf(self, pdf_path: str):
        try:
            # Read into memory: the preview never keeps the file open, so the PDF
            # can be rewritten (Delete Tag / undo / export) while it is shown.
            with open(pdf_path, "rb") as _f:
                _data = _f.read()
            if getattr(self, "doc", None) is not None:
                try:
                    self.doc.close()
                except Exception:
                    pass
            self.doc = fitz.open(stream=_data, filetype="pdf")
            self.pdf_path = pdf_path
            self.current_page_idx = 0
            self.zoom_level = 1.0
            self.selected_element_ids = []
            self.extra_selected_elements = []
            if hasattr(self, 'view') and hasattr(self.view, '_clear_active_selection'):
                self.view._clear_active_selection()
            self.render_page()
        except Exception as e:
            print(f"Error loading PDF: {e}")

    def render_page(self):
        if hasattr(self, 'page_items'):
            for p_idx, p_data in self.page_items.items():
                if p_data.get('pixmap_item'):
                    p_data['pixmap_item'] = None
                p_data['loaded'] = False
                
        self.scene.clear()
        self.page_rects = []
        
        if not self.doc:
            self.update_controls()
            return

        current_y_offset = 0
        factor = 2.0 * self.zoom_level
        
        self.page_items = {}

        # PRE-COMPUTE ELEMENTS BY PAGE TO AVOID O(N^2) HANGS ON LARGE PDFS
        def get_all_elements(elements):
            for el in elements:
                yield el
                if 'kids' in el:
                    yield from get_all_elements(el['kids'])
        
        all_els = list(get_all_elements(self.elements))
        
        # Deduplicate extra_selected_elements
        # Prefer elements from self.elements because they contain the latest edits!
        existing_ids = set()
        for el in all_els:
            el_id = el.get('id') or el.get('uuid') or el.get('node_id')
            if el_id: existing_ids.add(str(el_id))
            
        if hasattr(self, 'extra_selected_elements') and self.extra_selected_elements:
            for el in self.extra_selected_elements:
                el_id = el.get('id') or el.get('uuid') or el.get('node_id')
                if el_id and str(el_id) not in existing_ids:
                    all_els.append(el)
                    
        # Group by page
        elements_by_page = {}
        for el in all_els:
            page_num = el.get('metadata', {}).get('page') or el.get('page_number')
            try:
                page_num = int(page_num)
                elements_by_page.setdefault(page_num, []).append(el)
            except:
                pass

        for p_idx in range(len(self.doc)):
            page = self.doc.load_page(p_idx)
            page_height_pt = page.rect.height
            page_width_pt = page.rect.width
            
            px_h = page_height_pt * factor
            px_w = page_width_pt * factor
            
            page_bg = QGraphicsRectItem(0, 0, px_w, px_h)
            page_bg.setBrush(QBrush(QColor("white")))
            page_bg.setPen(QPen(Qt.NoPen))
            page_bg.setPos(0, current_y_offset)
            page_bg.setZValue(-2)
            self.scene.addItem(page_bg)
            
            self.page_rects.append({
                'page_idx': p_idx,
                'y_offset': current_y_offset,
                'height_pt': page_height_pt,
                'width_pt': page_width_pt,
                'factor': factor
            })
            
            self.page_items[p_idx] = {
                'bg_item': page_bg,
                'pixmap_item': None,
                'loaded': False,
                'y_offset': current_y_offset
            }
            
            # Retrieve elements just for this page in O(1)
            page_els = elements_by_page.get(p_idx + 1, [])
            for el in page_els:
                    
                # Skip drawing bounding boxes for structural wrappers (Acrobat-style)
                sem_type = el.get('semantic', {}).get('type') or el.get('type')
                if sem_type in ('table', 'table_row', 'list', 'list_item', 'toc', 'TOC', 'objr', 'OBJR'):
                    continue
                    
                bbox = el.get('geometry', {}).get('bbox') or el.get('bounding_box')
                if bbox and len(bbox) == 4:
                    x0_pt, pdf_y0, x1_pt, pdf_y1 = bbox
                    top_y0_pt = page_height_pt - pdf_y1
                    top_y1_pt = page_height_pt - pdf_y0
                    
                    px_x0 = x0_pt * factor
                    px_y0 = top_y0_pt * factor + current_y_offset
                    px_w = (x1_pt - x0_pt) * factor
                    px_h_el = (top_y1_pt - top_y0_pt) * factor
                    
                    rect = QRectF(px_x0, px_y0, px_w, px_h_el)
                    el_id = el.get('id') or el.get('uuid') or el.get('node_id')
                    is_selected = (el_id in self.selected_element_ids) if hasattr(self, 'selected_element_ids') else False
                    
                    if is_selected:
                        pen = QPen(QColor(255, 0, 0, 255), 2)
                        brush = QBrush(QColor(255, 0, 0, 50))
                    else:
                        pen = QPen(QColor(0, 120, 215, 200), 2)
                        brush = QBrush(QColor(0, 120, 215, 30))
                        
                    rect_item = DraggableBoundingBox(rect, el_id, pen, brush, self)
                    area = px_w * px_h_el
                    if is_selected:
                        rect_item.setZValue(999999.0) # Always on top when selected
                    else:
                        rect_item.setZValue(1.0 / max(1.0, area))
                    self.scene.addItem(rect_item)
                    
                    if is_selected:
                        self.view.ensureVisible(rect_item, 50, 50)
                        
                    type_str = el.get('semantic', {}).get('type') or el.get('type', 'Unknown')
                    rect_item.setToolTip(f"{type_str} (ID: {el_id})")
            current_y_offset += px_h + 20

        max_w = 0
        if hasattr(self, 'page_rects') and self.page_rects:
            for p in self.page_rects:
                w = p.get('width_pt', 0) * p['factor']
                if w > max_w: max_w = w
            self.scene.setSceneRect(0, 0, max_w, current_y_offset)
        
        self.update_controls()
        
        # Trigger an initial load for visible pages
        self._on_scroll()

        
    def load_elements(self, elements: list):
        self.elements = elements
        self.selected_element_ids = []
        self.extra_selected_elements = []
        if hasattr(self, 'view') and hasattr(self.view, '_clear_active_selection'):
            self.view._clear_active_selection()
        self.render_page()
        
    def select_element(self, element_id, page_number: int, extra_elements=None):
        if isinstance(element_id, list):
            self.selected_element_ids = element_id
        else:
            self.selected_element_ids = [element_id] if element_id is not None else []
        self.extra_selected_elements = extra_elements or []
        # We don't change current_page_idx anymore because it's continuous
        self.render_page()

    def _scroll_to_page(self, p_idx):
        if not self.doc or not hasattr(self, 'page_rects') or p_idx < 0 or p_idx >= len(self.page_rects):
            return
        y_offset = self.page_rects[p_idx]['y_offset']
        self.view.verticalScrollBar().setValue(int(y_offset))
        self.current_page_idx = p_idx
        self.update_controls()

    def prev_page(self):
        if self.current_page_idx > 0:
            self._scroll_to_page(self.current_page_idx - 1)

    def next_page(self):
        if self.doc and self.current_page_idx < len(self.doc) - 1:
            self._scroll_to_page(self.current_page_idx + 1)

    def goto_page_input(self):
        try:
            p_num = int(self.page_input.text())
            if self.doc and 1 <= p_num <= len(self.doc):
                self._scroll_to_page(p_num - 1)
            else:
                self.update_controls() # reset to valid
        except ValueError:
            self.update_controls()
            
    def _on_scroll(self):
        try:
            if not self.doc or not hasattr(self, 'page_rects'): return
            scroll_y = self.view.verticalScrollBar().value()
            viewport_height = self.view.viewport().height()
            
            visible_top = scroll_y
            visible_bottom = scroll_y + viewport_height
            
            # Add 1 viewport height margin above and below for loading
            load_top = visible_top - viewport_height
            load_bottom = visible_bottom + viewport_height
            
            best_idx = 0
            factor = 2.0 * self.zoom_level
            mat = fitz.Matrix(factor, factor)
            
            for p in self.page_rects:
                p_idx = p['page_idx']
                y_start = p['y_offset']
                px_h = p['height_pt'] * p['factor']
                y_end = y_start + px_h
                
                if visible_top >= y_start - 20 and visible_top <= y_end:
                    best_idx = p_idx
                    
                if y_end >= load_top and y_start <= load_bottom:
                    self._load_page_image(p_idx, mat)
                else:
                    self._unload_page_image(p_idx)
                    
            if self.current_page_idx != best_idx:
                self.current_page_idx = best_idx
                self.update_controls()
        except RuntimeError:
            pass

    def _load_page_image(self, p_idx, mat):
        if not hasattr(self, 'page_items'): return
        p_data = self.page_items.get(p_idx)
        if not p_data or p_data['loaded']: return
        
        try:
            page = self.doc.load_page(p_idx)
            pix = page.get_pixmap(matrix=mat)
            fmt = QImage.Format_RGBA8888 if pix.alpha else QImage.Format_RGB888
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, fmt)
            qpix = QPixmap.fromImage(img)
            
            pix_item = QGraphicsPixmapItem(qpix)
            pix_item.setPos(0, p_data['y_offset'])
            pix_item.setZValue(-1)
            self.scene.addItem(pix_item)
            
            p_data['pixmap_item'] = pix_item
            p_data['loaded'] = True
        except Exception as e:
            print(f"Error lazy-loading page {p_idx}: {e}")

    def _unload_page_image(self, p_idx):
        if not hasattr(self, 'page_items'): return
        p_data = self.page_items.get(p_idx)
        if not p_data or not p_data['loaded']: return
        
        if p_data['pixmap_item']:
            self.scene.removeItem(p_data['pixmap_item'])
            p_data['pixmap_item'] = None
            
        p_data['loaded'] = False

    def zoom_in(self):
        if self.doc:
            self.zoom_level = min(3.0, self.zoom_level + 0.25)
            self.render_page()
            self._scroll_to_page(self.current_page_idx)

    def zoom_out(self):
        if self.doc:
            self.zoom_level = max(0.25, self.zoom_level - 0.25)
            self.render_page()
            self._scroll_to_page(self.current_page_idx)

    def update_controls(self):
        if self.doc:
            self.lbl_zoom.setText(f" {int(self.zoom_level * 100)}% ")
            self.btn_zoom_in.setEnabled(self.zoom_level < 3.0)
            self.btn_zoom_out.setEnabled(self.zoom_level > 0.25)
            self.page_input.setText(str(self.current_page_idx + 1))
            self.lbl_page_total.setText(f" / {len(self.doc)} ")
        else:
            self.lbl_zoom.setText(" 100% ")
            self.btn_zoom_in.setEnabled(False)
            self.btn_zoom_out.setEnabled(False)
            self.page_input.setText("0")
            self.lbl_page_total.setText(" / 0 ")

    def _get_node_at_scene_pos(self, scene_pos):
        if not self.doc or not hasattr(self, 'page_rects'):
            return None
            
        target_p_idx = -1
        target_page_info = None
        for p_info in self.page_rects:
            y_start = p_info['y_offset']
            y_end = y_start + (p_info['height_pt'] * p_info['factor'])
            if y_start <= scene_pos.y() <= y_end:
                target_p_idx = p_info['page_idx']
                target_page_info = p_info
                break
                
        if target_p_idx == -1:
            return None
            
        page_height_pt = target_page_info['height_pt']
        factor = target_page_info['factor']
        x_pt = scene_pos.x() / factor
        top_y_pt = (scene_pos.y() - target_page_info['y_offset']) / factor
        pdf_y = page_height_pt - top_y_pt
        
        candidates = []
        for el in self.elements:
            page_num = el.get('metadata', {}).get('page') or el.get('page_number')
            if page_num != target_p_idx + 1:
                continue
            bbox = el.get('geometry', {}).get('bbox') or el.get('bounding_box')
            if bbox and len(bbox) == 4:
                x0, y0, x1, y1 = bbox
                if x0 <= x_pt <= x1 and y0 <= pdf_y <= y1:
                    area = (x1 - x0) * (y1 - y0)
                    candidates.append((area, el))
                    
        if candidates:
            candidates.sort(key=lambda x: x[0])
            best_el = candidates[0][1]
            el_id = best_el.get('id') or best_el.get('uuid') or best_el.get('node_id')
            return el_id
        return None

    def show_status(self, message: str):
        """Forward a status bar message to the main window if available."""
        if self._main_window is not None:
            self._main_window.statusBar.showMessage(message)

    def _get_page_num_for_node(self, el_id):
        for el in self.elements:
            if str(el.get('id')) == str(el_id) or str(el.get('uuid')) == str(el_id) or str(el.get('node_id')) == str(el_id):
                return el.get('metadata', {}).get('page') or el.get('page_number') or 1
        return 1

    def on_canvas_clicked(self, scene_pos):
        el_id = self._get_node_at_scene_pos(scene_pos)
        if el_id is not None:
            self.node_selected_signal.emit(el_id, self._get_page_num_for_node(el_id))

    def on_canvas_right_clicked(self, scene_pos, global_pos):
        el_id = self._get_node_at_scene_pos(scene_pos)
        if el_id is not None:
            self.node_right_clicked_signal.emit(el_id, self._get_page_num_for_node(el_id), global_pos)

    def on_tag_region_selected(self, rect: QRectF, role=None):
        if not self.doc:
            return

        page = self.doc.load_page(self.current_page_idx)
        page_height_pt = page.rect.height
        factor = 2.0 * self.zoom_level

        px_x0 = rect.left()
        px_y0 = rect.top()
        px_w = rect.width()
        px_h = rect.height()

        x0_pt = px_x0 / factor
        top_y0_pt = px_y0 / factor
        w_pt = px_w / factor
        h_pt = px_h / factor

        x1_pt = x0_pt + w_pt
        top_y1_pt = top_y0_pt + h_pt

        pdf_y0 = page_height_pt - top_y1_pt
        pdf_y1 = page_height_pt - top_y0_pt

        bbox = [x0_pt, pdf_y0, x1_pt, pdf_y1]

        self.tag_region_selected_signal.emit(bbox, self.current_page_idx + 1, role)

    def on_bbox_dragged(self, el_id, scene_rect: QRectF):
        if not self.doc:
            return
            
        # Search recursively through kids to find the element's page_number
        def find_page_num(elements, target_id):
            for el in elements:
                if str(el.get('id')) == str(target_id) or str(el.get('node_id')) == str(target_id) or str(el.get('uuid')) == str(target_id):
                    return el.get('metadata', {}).get('page') or el.get('page_number', None)
                if el.get('kids'):
                    result = find_page_num(el['kids'], target_id)
                    if result is not None:
                        return result
            return None
            
        actual_page_num = find_page_num(self.elements, el_id) or 1
                
        actual_page_idx = actual_page_num - 1
        if actual_page_idx < 0 or actual_page_idx >= len(self.doc):
            actual_page_idx = self.current_page_idx

        page = self.doc.load_page(actual_page_idx)
        page_height_pt = page.rect.height
        factor = 2.0 * self.zoom_level

        y_offset = 0
        if hasattr(self, 'page_rects') and actual_page_idx < len(self.page_rects):
            y_offset = self.page_rects[actual_page_idx]['y_offset']

        px_x0 = scene_rect.left()
        px_y0 = scene_rect.top() - y_offset
        px_w = scene_rect.width()
        px_h = scene_rect.height()

        x0_pt = px_x0 / factor
        top_y0_pt = px_y0 / factor
        w_pt = px_w / factor
        h_pt = px_h / factor

        x1_pt = x0_pt + w_pt
        top_y1_pt = top_y0_pt + h_pt

        pdf_y0 = page_height_pt - top_y1_pt
        pdf_y1 = page_height_pt - top_y0_pt

        bbox = [round(x0_pt, 3), round(pdf_y0, 3), round(x1_pt, 3), round(pdf_y1, 3)]

        self.bbox_modified_signal.emit(el_id, bbox, actual_page_num)
