from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, 
    QLineEdit, QComboBox, QCheckBox, QLabel, 
    QPushButton, QGroupBox
)
from PySide6.QtCore import Qt

LANGUAGE_MAP = {
    "English (United States)": "en-US",
    "English (United Kingdom)": "en-GB",
    "French (France)": "fr-FR",
    "German (Germany)": "de-DE",
    "Spanish (Spain)": "es-ES",
    "Italian (Italy)": "it-IT",
    "Portuguese (Brazil)": "pt-BR",
    "Dutch (Netherlands)": "nl-NL",
    "Russian (Russia)": "ru-RU",
    "Japanese (Japan)": "ja-JP",
    "Chinese (Simplified)": "zh-CN",
    "Chinese (Traditional)": "zh-TW"
}

class DocumentPropertiesDialog(QDialog):
    def __init__(self, parent=None, initial_metadata=None, default_title=None, engine_config=None):
        super().__init__(parent)
        self.setWindowTitle("Document Properties")
        self.setMinimumWidth(400)
        
        self.initial_metadata = initial_metadata or {}
        self.default_title = default_title or ""
        self.engine_config = engine_config or {}
        
        self.init_ui()
        self.populate_fields()
        
    def init_ui(self):
        layout = QVBoxLayout(self)
        
        # Document Metadata Group
        doc_group = QGroupBox("Document")
        doc_layout = QFormLayout(doc_group)
        
        self.title_input = QLineEdit()
        self.author_input = QLineEdit()
        self.subject_input = QLineEdit()
        self.keywords_input = QLineEdit()
        
        self.title_input.setPlaceholderText("Document title (shown in the PDF viewer's title bar)")
        self.author_input.setPlaceholderText("Author")
        doc_layout.addRow("Title:", self.title_input)
        doc_layout.addRow("Author:", self.author_input)
        
        layout.addWidget(doc_group)
        
        # Only Title and Author are editable. The other values are kept (not shown):
        # language / display-document-title come from the project or engine.json.
        self.language_combo = QComboBox()
        self.language_combo.addItems(list(LANGUAGE_MAP.keys()))
        self.language_combo.hide()
        self.display_doc_title_check = QCheckBox("Display document title")
        self.display_doc_title_check.hide()
        
        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        
        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self.accept)
        apply_btn.setDefault(True)
        
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(apply_btn)
        
        layout.addLayout(btn_layout)

    def populate_fields(self):
        # Title
        if "title" in self.initial_metadata:
            self.title_input.setText(self.initial_metadata["title"] or "")
        else:
            self.title_input.setText(self.default_title)
            
        self.author_input.setText(self.initial_metadata.get("author", ""))
        self.subject_input.setText(self.initial_metadata.get("subject", ""))
        self.keywords_input.setText(self.initial_metadata.get("keywords", ""))
        
        # Language
        lang_code = self.initial_metadata.get("language")
        if not lang_code:
            lang_code = self.engine_config.get("language", "en-US")
            
        # Find UI name for code
        for name, code in LANGUAGE_MAP.items():
            if code == lang_code:
                self.language_combo.setCurrentText(name)
                break
                
        # PDF/UA display_doc_title
        if "display_doc_title" in self.initial_metadata:
            self.display_doc_title_check.setChecked(bool(self.initial_metadata["display_doc_title"]))
        else:
            pdfua_cfg = self.engine_config.get("pdfua", {})
            self.display_doc_title_check.setChecked(bool(pdfua_cfg.get("display_doc_title", True)))

    def get_metadata(self) -> dict:
        # Hidden fields keep their stored values (nothing the user cannot see is changed)
        meta = dict(self.initial_metadata)
        meta["title"] = self.title_input.text().strip()
        meta["author"] = self.author_input.text().strip()
        if not meta.get("language"):
            meta["language"] = self.engine_config.get("language", "en-US")
        meta.setdefault("display_doc_title", True)
        return meta
