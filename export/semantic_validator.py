# GUI/export/semantic_validator.py

class SemanticValidator:
    def __init__(self):
        self.warnings = []
        self.errors = []
        self.seen_ids = set()
        self.seen_reading_orders = set()

    def validate(self, doc_json) -> bool:
        if doc_json.get("type") != "document":
            self.errors.append("Root element is not type='document'")
            return False

        if "metadata" not in doc_json:
            self.warnings.append("Missing metadata in document root")
            
        for page in doc_json.get("pages", []):
            page_num = page.get("page_number")
            if not page_num:
                self.errors.append("Missing page_number in pages array")
                continue

            for el in page.get("elements", []):
                self._validate_element(el, page_num)
                
        return len(self.errors) == 0

    def _validate_element(self, el, page_num: int):
        el_id = el.get("id")
        if el_id:
            if el_id in self.seen_ids:
                self.errors.append(f"Duplicate ID {el_id} on page {page_num}")
            self.seen_ids.add(el_id)
            
        ro = el.get("reading_order")
        if ro:
            if ro in self.seen_reading_orders:
                self.errors.append(f"Duplicate reading_order {ro} on page {page_num}")
            self.seen_reading_orders.add(ro)

        el_type = el.get("type")
        if "bounding_box" not in el and el_type not in ("table", "list", "table_row", "toc", "toci", "objr"):
            self.errors.append(f"Missing bounding_box for element {el_id} on page {page_num}")

        if el_type == "heading":
            if not el.get("heading_level"):
                self.errors.append(f"Invalid or missing heading_level for element {el_id}")
        elif el_type == "figure":
            if not el.get("alt_text"):
                self.warnings.append(f"Figure missing alt_text (ID {el_id})")
            if not el.get("image_path"):
                self.warnings.append(f"Figure missing image_path (ID {el_id})")
        elif el_type == "table":
            kids = el.get("kids", [])
            if not kids:
                self.warnings.append(f"Table without cells (ID {el_id})")
        elif el_type in ("figure_caption", "table_caption"):
            if not el.get("belongs_to"):
                self.warnings.append(f"Caption without parent object 'belongs_to' (ID {el_id})")
