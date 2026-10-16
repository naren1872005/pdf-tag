# GUI/export/accessibility_mapper.py

class AccessibilityMapper:
    MAPPING = {
        "heading": "H", # Normally H1-H6, logic should use heading_level
        "paragraph": "P",
        "form_label": "P",
        "figure": "Figure",
        "table": "Table",
        "table_row": "TR",
        "table_header_cell": "TH",
        "table_data_cell": "TD",
        "formula": "Formula",
        "Formula": "Formula",
        "hyperlink": "Link",
        "footnote_reference": "Reference",
        "footnote_note": "Note",
        "artifact": "Artifact",
        "list_item": "LI",
        "list": "L",
        "list_body": "LBody",
        "list_label": "Lbl",
        "figure_caption": "Caption",
        "table_caption": "Caption",
        "document": "Document",
        "form_line": "Artifact",
        "input_box": "Artifact",
        "rectangle": "Artifact",
        "visual_widget": "Artifact",
        "form": "Form",
        "toc": "TOC",
        "toci": "TOCI",
        "toc_item": "TOCI",
        "reference": "Reference",
        "link": "Link",
        "objr": "OBJR",
        "span": "Span",
        "Span": "Span"
    }

    @classmethod
    def get_tag(cls, el_type: str, el: dict = None) -> str:
        if el_type == "List":
            return "L"
            
        explicit_tags = {
            "Artifact", "Document", "Part", "Sect", "Div", "Art",
            "P", "H1", "H2", "H3", "H4", "H5", "H6", "H",
            "LI", "LBody", "Table", "TR", "TH", "TD", "Figure", "Formula",
            "Caption", "Form", "Link", "Note", "Reference", "L", "Lbl",
            "TOC", "TOCI", "OBJR", "Span"
        }
        if el_type in explicit_tags:
            return el_type
            
        if el_type == "heading" and el:
            level = el.get("heading_level", 1)
            return f"H{level}"
        return cls.MAPPING.get(el_type, "P")
