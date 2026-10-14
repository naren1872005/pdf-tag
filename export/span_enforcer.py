# GUI/export/span_enforcer.py
"""
Span Tagging & Accessibility Enforcer for PDF/UA-1.

Handles accurate inline Span tagging:
1. Unordered List Bullets — Lbl > Span with actual text "Bullet"
2. Field Labels — e.g. "Name:", "Date:" -> Span(label) + Span(value) inside parent P/LBody
3. Words Split Across Lines — Line-break hyphenated words -> Span(accessi-) + Span(bility)
4. Standalone Lines & Meaningful Fragments — Inline grouping inside semantic containers
5. Underlined Text — Text with underline formatting -> Span with Underline="true" / Layout attribute

Preserves existing semantic containers, heading hierarchies, list structures,
and reading order without regressions.
"""

import re
import unicodedata
from typing import Dict, Any, List, Optional, Tuple


class SpanEnforcer:
    """
    Reusable, modular Span tagging enforcer that extends the document structure
    with standard PDF/UA-1 Span elements.
    """

    BULLET_CHARS = set("•●○◦■□▪▫►▸‣⁃–—-*·✓✔➢➤→◆◇❖")
    ORDERED_MARKER_RE = re.compile(
        r'^\s*(?:\(?\d{1,4}(?:\.\d{1,4})*[\.\)\]]?|\(?[a-zA-Z][\.\)\]]|\(?[ivxlcdmIVXLCDM]{1,6}[\.\)\]])\s*$'
    )

    # Label pattern: Starts with uppercase letter, short token (1-4 words, <=35 chars) ending with colon
    LABEL_RE = re.compile(
        r'^([A-Z][A-Za-z0-9\s/&#\.\(\)\-]{0,32}:)\s+(\S.*)$',
        re.DOTALL
    )

    PROSE_STARTS = {
        "note that:", "remember that:", "such as:", "for example:",
        "including:", "notice that:", "see also:", "warning:", "caution:"
    }

    # Split word hyphenation at line end
    HYPHEN_SPLIT_RE = re.compile(
        r'(\b[A-Za-z]+[-\u2010\u2011])\s+([a-z]+(?:\b|[,\.\?!;]))'
    )

    def __init__(self):
        self.used_ids = set()
        self.changes = []

    def _collect_ids(self, els: List[dict]):
        for e in els:
            if e.get("id") is not None:
                self.used_ids.add(str(e.get("id")))
            self._collect_ids(e.get("kids") or [])

    def _new_id(self, base_id: Any, suffix: str) -> str:
        cand = f"{base_id}-{suffix}"
        n = 2
        while cand in self.used_ids:
            cand = f"{base_id}-{suffix}{n}"
            n += 1
        self.used_ids.add(cand)
        return cand

    def enforce(self, doc_json: dict, fitz_doc=None) -> dict:
        """
        Enforces Span tagging rules across all pages of the document JSON.
        """
        for page in doc_json.get("pages", []):
            self._collect_ids(page.get("elements", []))

        # Build drawing/underline cache per page if fitz_doc is provided
        page_underlines = {}
        if fitz_doc is not None:
            try:
                import fitz
                for p_idx, f_page in enumerate(fitz_doc):
                    page_underlines[p_idx + 1] = self._extract_page_underlines(f_page)
            except Exception:
                pass

        for page in doc_json.get("pages", []):
            p_num = page.get("page_number", 1)
            underlines = page_underlines.get(p_num, [])
            page["elements"] = self._process_elements(page.get("elements", []), underlines, p_num)

        return doc_json

    @staticmethod
    def _extract_page_underlines(page) -> List[Any]:
        """
        Extracts thin horizontal lines from page vector drawings that represent underlines.
        """
        underlines = []
        try:
            import fitz
            page_w = page.rect.width
            for d in page.get_drawings():
                r = d.get("rect")
                if not r:
                    continue
                w = r.width
                h = r.height
                # Exclude full-width page separators or decorative bars
                if w > 0.75 * page_w:
                    continue
                if h <= 3.5 and w >= 4.0:
                    underlines.append(r)
                for it in d.get("items", []):
                    if it[0] == "l":
                        p1, p2 = it[1], it[2]
                        if abs(p1.y - p2.y) <= 1.5:
                            lw = abs(p2.x - p1.x)
                            if 4.0 <= lw <= 0.75 * page_w:
                                underlines.append(fitz.Rect(min(p1.x, p2.x), min(p1.y, p2.y) - 1,
                                                            max(p1.x, p2.x), max(p1.y, p2.y) + 1))
        except Exception:
            pass
        return underlines

    def _process_elements(self, elements: List[dict], page_underlines: List[Any], page_num: int) -> List[dict]:
        processed = []
        for el in elements:
            res = self._process_node(el, page_underlines, page_num)
            if isinstance(res, list):
                processed.extend(res)
            elif res is not None:
                processed.append(res)
        return processed

    def _process_node(self, el: dict, page_underlines: List[Any], page_num: int) -> dict:
        t = str(el.get("type", "")).lower()
        tag = str(el.get("tag", "")).upper()

        # 1. Unordered List Bullet: Lbl > Span with actual text "Bullet"
        if t in ("list_label", "lbl") or tag == "LBL":
            return self._enforce_bullet_label(el)

        # 2. List Item (LI): Enforce bullet Span on child Lbl, and keep LBody clean
        if t in ("list_item", "li") or tag == "LI":
            return self._enforce_list_item(el, page_underlines, page_num)

        # 2b. List Body directly
        if t in ("list_body", "lbody") or tag == "LBODY":
            return self._clean_list_body(el, page_underlines, page_num)

        # Process kids recursively if present
        if el.get("kids"):
            el["kids"] = self._process_elements(el["kids"], page_underlines, page_num)
            return el

        # 3. Paragraph / Container inline Span tagging
        if t in ("paragraph", "p") or tag == "P":
            return self._enforce_paragraph_spans(el, page_underlines, page_num)

        return el

    # -------------------------------------------------------------------------
    # 1. Unordered List Bullet: Lbl > Span
    # -------------------------------------------------------------------------
    def is_unordered_bullet(self, marker_text: str) -> bool:
        m = (marker_text or "").strip()
        if not m:
            return False
        if self.ORDERED_MARKER_RE.match(m):
            return False
        if any(c in self.BULLET_CHARS for c in m):
            return True
        if len(m) == 1 and not m.isalnum():
            return True
        return False

    def _enforce_bullet_label(self, el: dict) -> dict:
        marker = (el.get("content") or "").strip()
        if not marker and el.get("kids"):
            for k in el["kids"]:
                c = (k.get("content") or "").strip()
                if c and c.lower() != "bullet":
                    marker = c
                    break
        if not marker or marker.lower() == "bullet":
            marker = el.get("_marker_char") or "•"

        if not self.is_unordered_bullet(marker):
            return el

        # Check if already has a Span kid
        kids = el.get("kids") or []
        has_span = any(str(k.get("type", "")).lower() in ("span",) or str(k.get("tag", "")).upper() == "SPAN" for k in kids)
        if has_span:
            for k in kids:
                if str(k.get("type", "")).lower() in ("span",) or str(k.get("tag", "")).upper() == "SPAN":
                    c = (k.get("content") or "").strip()
                    if not marker or marker.lower() == "bullet":
                        marker = c if (c and c.lower() != "bullet") else "•"
                    k["content"] = marker
                    k["actual_text"] = " Bullet "
                    k["tag"] = "Span"
            el["content"] = ""
            el.pop("actual_text", None)
            return el

        # Create Lbl > Span where Span holds the bullet point and actual_text=" Bullet "
        span_id = self._new_id(el.get("id", "lbl"), "sp")
        span_node = {
            "id": span_id,
            "type": "span",
            "tag": "Span",
            "content": marker,
            "actual_text": " Bullet ",
            "bounding_box": el.get("bounding_box"),
            "reading_order": el.get("reading_order"),
            "page_number": el.get("page_number"),
            "_synthetic": True,
            "_marker_char": marker
        }
        el["kids"] = [span_node]
        el["content"] = ""
        el.pop("actual_text", None)
        self.changes.append(f"Created Lbl > Span(content='{marker}', actual_text=' Bullet ')")
        return el

    def _clean_list_body(self, k: dict, page_underlines: List[Any], page_num: int) -> dict:
        # LBody holds its content directly: NO redundant P tag inside LBody!
        b_kids = k.get("kids") or []
        p_text_parts = []
        remaining_kids = []
        for bk in b_kids:
            bkt = str(bk.get("type", "")).lower()
            bktag = str(bk.get("tag", "")).upper()
            if bkt in ("paragraph", "p") or bktag == "P":
                txt = (bk.get("content") or "").strip()
                if txt:
                    p_text_parts.append(txt)
                if bk.get("kids"):
                    for inner in bk["kids"]:
                        remaining_kids.append(self._process_node(inner, page_underlines, page_num))
            else:
                remaining_kids.append(self._process_node(bk, page_underlines, page_num))

        if p_text_parts:
            p_text = " ".join(p_text_parts)
            curr = (k.get("content") or "").strip()
            if not curr:
                k["content"] = p_text
            elif curr != p_text and p_text not in curr:
                k["content"] = f"{curr} {p_text}".strip()
        k["tag"] = "LBody"
        k["kids"] = remaining_kids
        return k

    def _enforce_list_item(self, li: dict, page_underlines: List[Any], page_num: int) -> dict:
        kids = li.get("kids") or []
        fixed_kids = []
        for k in kids:
            kt = str(k.get("type", "")).lower()
            ktag = str(k.get("tag", "")).upper()
            if kt in ("list_label", "lbl") or ktag == "LBL":
                fixed_kids.append(self._enforce_bullet_label(k))
            elif kt in ("list_body", "lbody") or ktag == "LBODY":
                fixed_kids.append(self._clean_list_body(k, page_underlines, page_num))
            else:
                fixed_kids.append(self._process_node(k, page_underlines, page_num))
        li["kids"] = fixed_kids
        return li

    # -------------------------------------------------------------------------
    # Paragraph Spans: Labels, Split Words, Standalone Lines, Underlined Text
    # -------------------------------------------------------------------------
    def _enforce_paragraph_spans(self, el: dict, page_underlines: List[Any], page_num: int) -> dict:
        content = (el.get("content") or "").strip()
        if not content:
            return el

        # 5. Underlined Text Check
        spans_meta = el.get("_spans_meta") or []
        has_underline = el.get("underline") or any(s.get("underline") for s in spans_meta)
        if not has_underline and page_underlines:
            bbox = el.get("bounding_box")
            if bbox and len(bbox) == 4:
                # Check intersection with page_underlines
                # Note: page_underlines are in displayed fitz coordinates
                for ur in page_underlines:
                    # PDF coordinates: [x0, y0, x1, y1]
                    if ur.x0 <= bbox[2] + 2 and ur.x1 >= bbox[0] - 2:
                        has_underline = True
                        break

        if has_underline:
            return self._build_underlined_spans(el, content)

        # 2. Field Label Detection (e.g. "Name: John Smith", "Date: October 14, 2026")
        label_match = self.LABEL_RE.match(content)
        if label_match:
            label_part = label_match.group(1).strip()
            value_part = label_match.group(2).strip()
            if self._is_genuine_label(label_part, value_part):
                return self._build_label_value_spans(el, label_part, value_part)

        # 3. Words Split Across Lines (e.g. "accessi-" followed by "bility")
        split_match = self.HYPHEN_SPLIT_RE.search(content)
        if split_match:
            return self._build_split_word_spans(el, content, split_match)

        # 4. Standalone Lines & Line breaks requiring inline grouping
        if "\n" in content:
            lines = [line.strip() for line in content.split("\n") if line.strip()]
            if len(lines) >= 2:
                return self._build_multiline_spans(el, lines)
        elif el.get("is_standalone_line") or el.get("standalone"):
            return self._build_standalone_span(el, content)

        return el

    def _is_genuine_label(self, label_part: str, value_part: str) -> bool:
        if not value_part:
            return False
        words = label_part.split()
        if len(words) < 1 or len(words) > 4:
            return False
        if label_part.lower() in self.PROSE_STARTS:
            return False
        # Avoid bible verses (e.g. "John 3: 16") or pure time (e.g. "12: 30")
        if re.match(r'^\d+:\s*\d+$', f"{label_part} {value_part}"):
            return False
        return True

    def _build_label_value_spans(self, el: dict, label_part: str, value_part: str) -> dict:
        span_lbl_id = self._new_id(el["id"], "lbl")
        span_val_id = self._new_id(el["id"], "val")
        bbox = el.get("bounding_box")

        span_lbl = {
            "id": span_lbl_id,
            "type": "span",
            "tag": "Span",
            "content": label_part,
            "bounding_box": bbox,
            "reading_order": el.get("reading_order"),
            "page_number": el.get("page_number"),
            "is_field_label": True,
            "_synthetic": True
        }
        span_val = {
            "id": span_val_id,
            "type": "span",
            "tag": "Span",
            "content": value_part,
            "bounding_box": bbox,
            "reading_order": el.get("reading_order"),
            "page_number": el.get("page_number"),
            "is_field_value": True,
            "_synthetic": True
        }
        el["kids"] = [span_lbl, span_val]
        el["content"] = ""
        self.changes.append(f"Wrapped label '{label_part}' and value '{value_part}' in Spans")
        return el

    def _build_split_word_spans(self, el: dict, content: str, match: re.Match) -> dict:
        prefix_end = match.end(1)
        part1 = content[:prefix_end].strip()
        part2 = content[prefix_end:].strip()

        span1_id = self._new_id(el["id"], "w1")
        span2_id = self._new_id(el["id"], "w2")
        bbox = el.get("bounding_box")

        span1 = {
            "id": span1_id,
            "type": "span",
            "tag": "Span",
            "content": part1,
            "bounding_box": bbox,
            "reading_order": el.get("reading_order"),
            "page_number": el.get("page_number"),
            "_synthetic": True
        }
        span2 = {
            "id": span2_id,
            "type": "span",
            "tag": "Span",
            "content": part2,
            "bounding_box": bbox,
            "reading_order": el.get("reading_order"),
            "page_number": el.get("page_number"),
            "_synthetic": True
        }
        el["kids"] = [span1, span2]
        el["content"] = ""
        self.changes.append(f"Wrapped split word fragments '{part1}' and '{part2}' in Spans")
        return el

    def _build_multiline_spans(self, el: dict, lines: List[str]) -> dict:
        spans = []
        bbox = el.get("bounding_box")
        for idx, line in enumerate(lines, 1):
            sp_id = self._new_id(el["id"], f"ln{idx}")
            spans.append({
                "id": sp_id,
                "type": "span",
                "tag": "Span",
                "content": line,
                "bounding_box": bbox,
                "reading_order": el.get("reading_order"),
                "page_number": el.get("page_number"),
                "_synthetic": True
            })
        el["kids"] = spans
        el["content"] = ""
        self.changes.append(f"Split {len(lines)} standalone lines into Spans")
        return el

    def _build_standalone_span(self, el: dict, content: str) -> dict:
        sp_id = self._new_id(el["id"], "sp")
        span_node = {
            "id": sp_id,
            "type": "span",
            "tag": "Span",
            "content": content,
            "bounding_box": el.get("bounding_box"),
            "reading_order": el.get("reading_order"),
            "page_number": el.get("page_number"),
            "_synthetic": True
        }
        el["kids"] = [span_node]
        el["content"] = ""
        self.changes.append(f"Wrapped standalone line '{content}' in Span")
        return el

    def _build_underlined_spans(self, el: dict, content: str) -> dict:
        spans_meta = el.get("_spans_meta") or []
        underlined_texts = [s.get("text", "").strip() for s in spans_meta if s.get("underline") and s.get("text", "").strip()]

        if not underlined_texts:
            # Entire paragraph is marked underlined
            sp_id = self._new_id(el["id"], "u")
            span_node = {
                "id": sp_id,
                "type": "span",
                "tag": "Span",
                "content": content,
                "underline": True,
                "attributes": {"Underline": "true"},
                "bounding_box": el.get("bounding_box"),
                "reading_order": el.get("reading_order"),
                "page_number": el.get("page_number"),
                "_synthetic": True
            }
            el["kids"] = [span_node]
            el["content"] = ""
            self.changes.append(f"Wrapped entire underlined text in Span Underline='true'")
            return el

        # Segment content by the underlined ranges
        kids = []
        curr_text = content
        for u_text in underlined_texts:
            pos = curr_text.find(u_text)
            if pos < 0:
                continue
            pre = curr_text[:pos]
            if pre.strip():
                sp_pre_id = self._new_id(el["id"], "txt")
                kids.append({
                    "id": sp_pre_id,
                    "type": "span",
                    "tag": "Span",
                    "content": pre.strip(),
                    "bounding_box": el.get("bounding_box"),
                    "reading_order": el.get("reading_order"),
                    "page_number": el.get("page_number"),
                    "_synthetic": True
                })
            sp_u_id = self._new_id(el["id"], "u")
            kids.append({
                "id": sp_u_id,
                "type": "span",
                "tag": "Span",
                "content": u_text,
                "underline": True,
                "attributes": {"Underline": "true"},
                "bounding_box": el.get("bounding_box"),
                "reading_order": el.get("reading_order"),
                "page_number": el.get("page_number"),
                "_synthetic": True
            })
            curr_text = curr_text[pos + len(u_text):]

        if curr_text.strip():
            sp_post_id = self._new_id(el["id"], "txt")
            kids.append({
                "id": sp_post_id,
                "type": "span",
                "tag": "Span",
                "content": curr_text.strip(),
                "bounding_box": el.get("bounding_box"),
                "reading_order": el.get("reading_order"),
                "page_number": el.get("page_number"),
                "_synthetic": True
            })

        if kids:
            el["kids"] = kids
            el["content"] = ""
            self.changes.append(f"Segmented paragraph with underlined Span(s)")
        return el
