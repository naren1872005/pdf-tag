# GUI/export/semantic_normalizer.py

class SemanticNormalizer:
    def normalize(self, doc_json):
        for page in doc_json.get("pages", []):
            elements = page.get("elements", [])
            
            # Sort elements by reading order safely casting to int, inheriting from first kid if container
            def get_ro(x):
                ro = x.get("reading_order")
                if ro is not None and str(ro).isdigit():
                    return int(ro)
                kids = x.get("kids", [])
                if kids:
                    first_ro = kids[0].get("reading_order")
                    if first_ro is not None and str(first_ro).isdigit():
                        return int(first_ro)
                    if kids[0].get("id") is not None and str(kids[0].get("id")).isdigit():
                        return int(kids[0].get("id"))
                if x.get("id") is not None and str(x.get("id")).isdigit():
                    return int(x.get("id"))
                return 999999

            elements.sort(key=get_ro)
            
            normalized_elements = []
            current_list_items = []
            
            def flush_list():
                if current_list_items:
                    normalized_elements.append({
                        "type": "list",
                        "tag": "L",
                        "reading_order": current_list_items[0].get("reading_order"),
                        # Own id: sharing the first LI's id made L and LI collide (same /ID, same MCIDs)
                        "id": f"{current_list_items[0].get('id')}-L",
                        "kids": list(current_list_items)
                    })
                    current_list_items.clear()
            
            for el in elements:
                # Filter out ghost empty elements
                if not (el.get("content") or "").strip() and not el.get("kids") and el.get("type") not in ("figure", "table"):
                    continue
                el_type = el.get("type")
                if el_type == "table":
                    self._normalize_table(el)
                    
                if el_type == "list_item":
                    self._normalize_list_item(el)
                    current_list_items.append(el)
                    continue
                elif el_type == "formula" or el.get("tag") == "Formula":
                    flush_list()
                    f_id = el.get("id")
                    p_wrap = {
                        "id": f"{f_id}-P" if f_id is not None else None,
                        "type": "paragraph",
                        "tag": "P",
                        "content": el.get("content") or el.get("text") or "",
                        "alt_text": el.get("alt_text") or "",
                        "bounding_box": el.get("bounding_box"),
                        "reading_order": el.get("reading_order"),
                        "kids": [el],
                        "confidence": 1.0
                    }
                    normalized_elements.append(p_wrap)
                    continue
                else:
                    flush_list()
                    normalized_elements.append(el)
            
            flush_list()
            page["elements"] = normalized_elements
            
        return doc_json

    def _normalize_table(self, table_el):
        cells = table_el.get("kids", [])
        if not cells: return
        if any(c.get("type") in ("table_row", "TR") or c.get("tag") == "TR" for c in cells):
            return
        
        def get_top(c):
            bbox = c.get("bounding_box")
            return bbox[3] if isinstance(bbox, list) and len(bbox) >= 4 else 0

        # Sort by Y coordinate (bottom-up PDF coordinates: top is larger y1)
        cells.sort(key=get_top, reverse=True)
        
        rows = []
        current_row = []
        current_y_top = None
        
        THRESHOLD = 10.0
        for cell in cells:
            top = get_top(cell)
            if current_y_top is None:
                current_y_top = top
                current_row.append(cell)
            elif abs(top - current_y_top) < THRESHOLD:
                current_row.append(cell)
            else:
                rows.append(current_row)
                current_row = [cell]
                current_y_top = top
        if current_row:
            rows.append(current_row)
            
        def get_left(c):
            bbox = c.get("bounding_box")
            return bbox[0] if isinstance(bbox, list) and len(bbox) >= 1 else 0

        # Sort each row by X coordinate (left ascending)
        for row in rows:
            row.sort(key=get_left)
            
        tr_kids = []
        for row in rows:
            # check if entire row is headers
            is_all_header = all(c.get("type") == "table_header_cell" for c in row)
            # but usually we just wrap in TR anyway
            tr_kids.append({
                "type": "table_row",
                "tag": "TR",
                "kids": row
            })
        table_el["kids"] = tr_kids

    def _normalize_list_item(self, el):
        if el.get("kids"):
            return
        import re
        content = el.get("content", "").strip()
        bullet_pattern = r'^([\u2022\u25CF\u25CB\u25A0\u25A1\u25AA\u25AB\u25BA\u25B8\u2013\-]|(?:(?:\d+|[a-zA-Z]|[ivxlcdmIVXLCDM]{1,4})[.)]?)|(?:\([a-zA-Z0-9]{1,4}\))|(?:\[[a-zA-Z0-9]{1,4}\]))\s+(.*)$'
        match = re.match(bullet_pattern, content, re.DOTALL)
        if match:
            el["list_label"] = match.group(1).strip()
            el["list_body"] = match.group(2).strip()
        else:
            el["list_label"] = None
            el["list_body"] = content
