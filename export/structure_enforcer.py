# GUI/export/structure_enforcer.py
"""
Enforces PDF/UA-valid nesting for Lists and Tables of Contents on the legacy
semantic JSON (the {"pages": [{"elements": [...]}]} tree used by the exporter).

It runs right before tagging, so whatever Gemini or the rule engine produced,
the tag tree always ends up as:

    L
    └── LI
        ├── Lbl            (bullet / number, when present)
        └── LBody
            ├── ...text / P / Figure...
            └── L          (nested list lives INSIDE LBody, never directly in LI)
                └── LI ...

    TOC
    └── TOCI
        ├── Lbl            (optional entry number)
        ├── Reference
        │   └── Link       (added at export when the target heading is found)
        └── TOC            (nested TOC lives inside the parent TOCI, never TOCI-in-TOCI)
            └── TOCI ...

Rules enforced
--------------
* L contains only LI.          stray L -> moved into previous LI's LBody
                               stray text -> wrapped in LI > LBody
* LI contains only Lbl, LBody. LI-in-LI -> nested L inside LBody
                               L-in-LI  -> moved into LBody
                               LI text  -> split into Lbl (marker) + LBody (rest)
* list_item outside an L       -> consecutive items wrapped in a new L
* TOC contains only TOCI.      stray TOC -> moved into previous TOCI
                               stray text/Reference/Link -> wrapped in TOCI
* TOCI contains Lbl, Reference, P, TOC. TOCI-in-TOCI -> nested TOC inside the TOCI
                               TOCI text -> moved into a Reference
                               bare Link -> wrapped in Reference
* toci outside a TOC           -> consecutive entries wrapped in a new TOC
"""
import re
import unicodedata

LIST_TYPES = {"list", "List", "L"}
LI_TYPES = {"list_item", "LI"}
LBL_TYPES = {"list_label", "Lbl"}
LBODY_TYPES = {"list_body", "LBody"}
TOC_TYPES = {"toc", "TOC"}
TOCI_TYPES = {"toci", "toc_item", "toc_entry", "TOCI"}
REF_TYPES = {"reference", "Reference"}
TABLE_TYPES = {"table", "Table"}
ROW_TYPES = {"table_row", "TR"}
TH_TYPES = {"table_header_cell", "TH"}
TD_TYPES = {"table_data_cell", "TD", "table_cell"}
CELL_TYPES = TH_TYPES | TD_TYPES
NOTE_TYPES = {"footnote_note", "endnote", "note", "Note"}
NOTE_MARK_RE = re.compile(r'^\s*((?:\d{1,4}|[*\u2020\u2021\u00A7\u00B6]{1,3}|[a-z]|[ivxlcdm]{1,5})[.)]?)\s+(\S.*)$', re.DOTALL | re.I)
LINK_TYPES = {"link", "hyperlink", "Link"}

BULLET_CHARS = "•●○◦■□▪▫►▸‣⁃–—-*·✓✔➢➤→◆◇❖"
MARKER_RE = re.compile(
    r'^\s*('
    r'[' + re.escape(BULLET_CHARS) + r']'
    r'|(?:\(?(?:\d{1,3}(?:\.\d{1,3})*|[a-zA-Z]|[ivxlcdmIVXLCDM]{1,6})[.)\]:])'
    r'|(?:\[[a-zA-Z0-9.]{1,6}\])'
    r')\s+(.*)$', re.DOTALL)


def _type(el):
    return el.get("type") or (el.get("semantic") or {}).get("type") or ""


def _union(boxes):
    boxes = [b for b in boxes if isinstance(b, (list, tuple)) and len(b) == 4]
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _subtree_boxes(el):
    out = []
    b = el.get("bounding_box")
    if isinstance(b, (list, tuple)) and len(b) == 4:
        out.append(list(b))
    for k in el.get("kids") or []:
        out.extend(_subtree_boxes(k))
    return out


def split_marker(text):
    """'• Item text' -> ('•', 'Item text'); '2. Step' -> ('2.', 'Step'); no marker -> ('', text)."""
    t = (text or "").strip()
    m = MARKER_RE.match(t)
    if m and m.group(2).strip():
        return m.group(1), m.group(2).strip()
    if t and t[0] in BULLET_CHARS and len(t) > 1:
        return t[0], t[1:].strip()
    return "", t


def norm_text(t):
    t = unicodedata.normalize("NFKD", t or "")
    return "".join(c.lower() for c in t if c.isalnum())


class StructureEnforcer:
    def __init__(self):
        self.used_ids = set()
        self.changes = []

    # ------------------------------------------------------------------ ids
    def _collect_ids(self, els):
        for e in els:
            if e.get("id") is not None:
                self.used_ids.add(str(e.get("id")))
            self._collect_ids(e.get("kids") or [])

    def _new_id(self, base, suffix):
        cand = f"{base}-{suffix}"
        n = 2
        while cand in self.used_ids:
            cand = f"{base}-{suffix}{n}"
            n += 1
        self.used_ids.add(cand)
        return cand

    def _make(self, typ, base_el, suffix, kids=None, content="", bbox=None):
        tag_map = {
            "list": "L",
            "list_item": "LI",
            "list_label": "Lbl",
            "list_body": "LBody",
            "toc": "TOC",
            "toci": "TOCI",
            "reference": "Reference",
            "paragraph": "P",
            "table": "Table",
            "table_row": "TR",
            "table_header_cell": "TH",
            "table_data_cell": "TD",
            "heading": "H1",
            "quote": "BlockQuote",
            "note": "Note"
        }
        node = {
            "type": typ,
            "tag": tag_map.get(typ, typ.upper() if len(typ) <= 4 else typ.title()),
            "id": self._new_id(base_el.get("id", "n"), suffix),
            "reading_order": base_el.get("reading_order"),
            "page_number": base_el.get("page_number"),
            "content": content,
            "kids": kids or [],
            "_synthetic": True,
        }
        b = bbox or _union([bb for k in (kids or []) for bb in _subtree_boxes(k)]) or base_el.get("bounding_box")
        if b:
            node["bounding_box"] = list(b)
        return node

    # ------------------------------------------------------------- driver
    def enforce(self, doc_json):
        enforce_heading_hierarchy(doc_json)
        for page in doc_json.get("pages", []):
            self._collect_ids(page.get("elements", []))
        for page in doc_json.get("pages", []):
            page["elements"] = self._fix_children(None, page.get("elements", []))
        self._link_multi_page_lists(doc_json)
        return doc_json

    def _fix_children(self, parent, kids):
        fixed_kids = []
        for k in (kids or []):
            res = self._fix_node(k)
            if isinstance(res, list):
                fixed_kids.extend(res)
            elif res is not None:
                if parent is None and (_type(res) == "formula" or res.get("tag") == "Formula"):
                    wrapped_p = self._make("paragraph", res, "P", kids=[res], content=res.get("content") or "")
                    wrapped_p["alt_text"] = res.get("alt_text") or res.get("content") or ""
                    fixed_kids.append(wrapped_p)
                else:
                    fixed_kids.append(res)
        ptype = _type(parent) if parent is not None else ""
        # Group orphan list items / TOC entries that are not under their container
        if ptype not in LIST_TYPES:
            fixed_kids = self._group(fixed_kids, LI_TYPES | LBL_TYPES | LBODY_TYPES, self._wrap_list, skip_parent=ptype in LI_TYPES)
        if ptype not in TOC_TYPES:
            fixed_kids = self._group(fixed_kids, TOCI_TYPES, self._wrap_toc, skip_parent=ptype in TOCI_TYPES)
        return fixed_kids

    def _group(self, kids, types, wrapper, skip_parent):
        if skip_parent:
            return kids  # LI / TOCI handle their own kids
        out, run = [], []
        for k in kids:
            if _type(k) in types:
                run.append(k)
            else:
                if run:
                    out.append(wrapper(run))
                    run = []
                out.append(k)
        if run:
            out.append(wrapper(run))
        return out

    def _wrap_list(self, run):
        items = []
        pending = []
        for k in run:
            t = _type(k)
            if t in LI_TYPES:
                if pending:
                    items.append(self._fix_li(self._make("list_item", pending[0], "LI", kids=pending)))
                    pending = []
                items.append(k)
            else:  # stray Lbl / LBody
                if t in LBL_TYPES and pending:
                    items.append(self._fix_li(self._make("list_item", pending[0], "LI", kids=pending)))
                    pending = []
                pending.append(k)
        if pending:
            items.append(self._fix_li(self._make("list_item", pending[0], "LI", kids=pending)))
        self.changes.append(f"wrapped {len(items)} list item(s) in L")
        root_l = self._make("list", run[0], "L", kids=[])
        return self._build_list_tree(root_l, items)

    def _wrap_toc(self, run):
        self.changes.append(f"wrapped {len(run)} TOC entr(ies) in TOC")
        return self._make("toc", run[0], "TOC", kids=run)

    # --------------------------------------------------------- per node
    def _fix_node(self, el):
        t = _type(el)
        if t in TOCI_TYPES and t != "toci":
            el["type"] = "toci"
            t = "toci"
        if t in LIST_TYPES:
            el["kids"] = [self._fix_node(k) for k in el.get("kids") or []]
            return self._fix_list(el)
        if t in LI_TYPES:
            el["kids"] = [self._fix_node(k) for k in el.get("kids") or []]
            return self._fix_li(el)
        if t in TOC_TYPES:
            el["kids"] = [self._fix_node(k) for k in el.get("kids") or []]
            return self._fix_toc(el)
        if t in TOCI_TYPES:
            el["kids"] = [self._fix_node(k) for k in el.get("kids") or []]
            return self._fix_toci(el)
        if t in NOTE_TYPES:
            if el.get("kids"):
                el["kids"] = self._fix_children(el, el["kids"])
            return self._fix_note(el)
        if t in TABLE_TYPES:
            if self._is_layout_table(el):
                unwrapped = self._unwrap_layout_table(el)
                return [self._fix_node(u) for u in unwrapped]
            if el.get("kids"):
                el["kids"] = self._fix_children(el, el["kids"])
            return self._fix_table(el)
        if el.get("kids"):
            el["kids"] = self._fix_children(el, el["kids"])
        return el

    def _analyze_item(self, item):
        label_txt = ""
        for k in item.get("kids") or []:
            if _type(k) in LBL_TYPES:
                label_txt = (k.get("content") or "").strip()
                break
        if not label_txt:
            label_txt = (item.get("list_label") or "").strip()
        if not label_txt:
            m, _ = split_marker(item.get("content") or "")
            label_txt = m.strip()

        m_type = "unknown"
        num_val = None

        if label_txt:
            if any(c in BULLET_CHARS for c in label_txt) or label_txt in BULLET_CHARS:
                m_type = "bullet"
            else:
                m_num = re.match(r'^[\[\(]?(\d+)[\]\)\.:]?$', label_txt)
                if m_num:
                    m_type = "numeric"
                    num_val = int(m_num.group(1))
                else:
                    m_alpha = re.match(r'^[\[\(]?([a-zA-Z])[\]\)\.:]?$', label_txt)
                    if m_alpha:
                        char = m_alpha.group(1)
                        if len(char) == 1:
                            m_type = "alpha_lower" if char.islower() else "alpha_upper"
                            num_val = ord(char.lower()) - ord('a') + 1

        bbox = item.get("bounding_box") or []
        x0 = bbox[0] if isinstance(bbox, (list, tuple)) and len(bbox) >= 1 else 0.0
        return label_txt, m_type, num_val, x0

    def _build_list_tree(self, root_list, raw_items):
        if not raw_items:
            root_list["kids"] = []
            return root_list

        first_li = next((i for i in raw_items if _type(i) in LI_TYPES), raw_items[0])
        first_lbl, first_mtype, first_num, first_x0 = self._analyze_item(first_li)
        root_list["kids"] = []

        stack = [{
            "level": 0,
            "list_node": root_list,
            "last_li": None,
            "marker_type": first_mtype,
            "last_num": first_num,
            "x0": first_x0
        }]

        for item in raw_items:
            t = _type(item)
            if t in LIST_TYPES:
                curr_li = stack[-1]["last_li"]
                if curr_li is not None:
                    self._body_of(curr_li)["kids"].append(item)
                    self._refresh_bbox(self._body_of(curr_li))
                else:
                    stack[-1]["list_node"]["kids"].append(item)
                continue

            if t not in LI_TYPES:
                curr_li = stack[-1]["last_li"]
                if curr_li is not None:
                    self._body_of(curr_li)["kids"].append(item)
                else:
                    stack[-1]["list_node"]["kids"].append(item)
                continue

            li = self._fix_li(item)
            lbl, m_type, num_val, x0 = self._analyze_item(li)

            top = stack[-1]

            # 1. Ancestor resumption check (pop)
            resumed_idx = None
            if len(stack) > 1:
                for idx in range(len(stack) - 2, -1, -1):
                    anc = stack[idx]
                    # Numeric sequence continuation (e.g. 3 -> 4)
                    if m_type == "numeric" and anc["marker_type"] == "numeric":
                        if anc["last_num"] is not None and num_val is not None:
                            if num_val == anc["last_num"] + 1 or (num_val > anc["last_num"] and num_val <= anc["last_num"] + 3):
                                resumed_idx = idx
                                break
                    # Alpha sequence continuation (e.g. a -> b)
                    if m_type in ("alpha_lower", "alpha_upper") and anc["marker_type"] == m_type:
                        if anc["last_num"] is not None and num_val is not None and num_val == anc["last_num"] + 1:
                            resumed_idx = idx
                            break
                    # Visual unindentation back to ancestor's column position
                    if abs(x0 - anc["x0"]) <= 6.0 and m_type == anc["marker_type"] and x0 < top["x0"] - 4.0:
                        resumed_idx = idx
                        break

            if resumed_idx is not None:
                while len(stack) > resumed_idx + 1:
                    popped = stack.pop()
                    self._refresh_bbox(popped["list_node"])
                top = stack[-1]

            # 2. Nested child level check (push)
            is_nested = False
            if top["last_li"] is not None:
                ordered_to_unordered = (top["marker_type"] == "numeric" and m_type == "bullet")
                submarker = (m_type in ("alpha_lower", "roman") and top["marker_type"] not in ("alpha_lower", "roman"))
                indented = (x0 >= top["x0"] + 6.0 and abs(x0 - top["x0"]) < 100.0)
                restarted_num = (m_type == "numeric" and num_val == 1 and (top["last_num"] or 0) >= 1 and indented)

                if ordered_to_unordered or submarker or indented or restarted_num:
                    is_nested = True

            if is_nested:
                parent_li = top["last_li"]
                parent_body = self._body_of(parent_li)
                sub_l = self._make("list", li, "L", kids=[])
                parent_body["kids"].append(sub_l)
                self.changes.append("nested L created inside parent LBody")

                new_level = {
                    "level": len(stack),
                    "list_node": sub_l,
                    "last_li": li,
                    "marker_type": m_type,
                    "last_num": num_val,
                    "x0": x0
                }
                stack.append(new_level)
                sub_l["kids"].append(li)
            else:
                top["list_node"]["kids"].append(li)
                top["last_li"] = li
                if num_val is not None:
                    top["last_num"] = num_val
                if m_type != "unknown":
                    top["marker_type"] = m_type

        while len(stack) > 1:
            popped = stack.pop()
            self._refresh_bbox(popped["list_node"])
        self._refresh_bbox(root_list)
        return root_list

    def _fix_list(self, L):
        raw_kids = L.get("kids") or []
        items = []
        trailing_non_list = []

        # Separate trailing non-list elements (e.g. content following the list)
        end_idx = len(raw_kids)
        while end_idx > 0:
            last_k = raw_kids[end_idx - 1]
            last_t = _type(last_k)
            if last_t in (LI_TYPES | LBL_TYPES | LBODY_TYPES | LIST_TYPES):
                break
            trailing_non_list.insert(0, last_k)
            end_idx -= 1

        for k in raw_kids[:end_idx]:
            t = _type(k)
            if t in LI_TYPES:
                items.append(k)
            elif t in LIST_TYPES:
                items.append(k)
            elif t in LBL_TYPES or t in LBODY_TYPES:
                if t in LBODY_TYPES and items and not self._has_body_content(items[-1]) and not self._label_of(items[-1]):
                    items[-1]["kids"].append(k)
                    items[-1] = self._fix_li(items[-1])
                else:
                    items.append(self._fix_li(self._make("list_item", k, "LI", kids=[k])))
            else:
                if (k.get("content") or "").strip() and not k.get("kids") and t not in ("figure",):
                    items.append(self._fix_li(self._make("list_item", k, "LI", kids=[], content=k.get("content", ""), bbox=k.get("bounding_box"))))
                    self.changes.append(f"wrapped stray {t} in LI")
                elif items:
                    self._body_of(items[-1])["kids"].append(k)
                else:
                    items.append(self._fix_li(self._make("list_item", k, "LI", kids=[k])))

        L = self._build_list_tree(L, items)
        L.pop("content", None)
        self._refresh_bbox(L)
        if trailing_non_list:
            return [L] + trailing_non_list
        return L

    def _label_of(self, li):
        for k in li.get("kids") or []:
            if _type(k) in LBL_TYPES:
                return k
        return None

    def _has_body_content(self, li):
        for k in li.get("kids") or []:
            if _type(k) in LBODY_TYPES and ((k.get("content") or "").strip() or k.get("kids")):
                return True
        return False

    def _body_of(self, li):
        for k in li.get("kids") or []:
            if _type(k) in LBODY_TYPES:
                k.setdefault("kids", [])
                return k
        body = self._make("list_body", li, "lbody", kids=[], bbox=li.get("bounding_box"))
        li.setdefault("kids", []).append(body)
        return body

    def _fix_li(self, li):
        li["type"] = "list_item"
        kids = li.get("kids") or []
        label = None
        body = None
        rest = []
        nested_items = []
        for k in kids:
            t = _type(k)
            if t in LBL_TYPES and label is None:
                label = k
            elif t in LBODY_TYPES and body is None:
                body = k
            elif t in LI_TYPES:
                nested_items.append(k)
                self.changes.append("LI directly in LI -> nested L in LBody")
            elif t in LBODY_TYPES:
                k["type"] = "paragraph"  # a second body is just more body text
                rest.append(k)
            elif t in LBL_TYPES:
                k["type"] = "paragraph"
                rest.append(k)
            else:
                rest.append(k)

        own = (li.get("content") or "").strip()
        if own:
            if label is None and body is None:
                marker, text = split_marker(own)
                if li.get("list_label") and not marker:
                    marker = str(li.get("list_label"))
                    if own.startswith(marker):
                        text = own[len(marker):].strip()
                if marker:
                    label = self._make("list_label", li, "lbl",
                                       bbox=li.get("label_bounding_box") or li.get("bounding_box"))
                    label["tag"] = "Lbl"
                    span_node = self._make("span", label, "span", content=marker,
                                           bbox=label.get("bounding_box"))
                    span_node["tag"] = "Span"
                    span_node["actual_text"] = " Bullet " if marker in ("•", "n", "■", "●") else marker
                    label["kids"] = [span_node]
                    label["content"] = ""
                body = self._make("list_body", li, "lbody", content=text, bbox=li.get("bounding_box"))
                body["tag"] = "LBody"
                for key in ("lines", "font", "font size", "analysis"):
                    if key in li:
                        body[key] = li[key]
                self.changes.append("split LI text into Lbl (with Span) + LBody")
            elif body is None:
                body = self._make("list_body", li, "lbody", content=own, bbox=li.get("bounding_box"))
                body["tag"] = "LBody"
            li["content"] = ""
            li.pop("lines", None)

        # Ensure existing label wraps its bullet in a Span child if missing
        if label is not None and not label.get("kids"):
            lbl_c = (label.get("content") or "").strip()
            if lbl_c:
                span_node = self._make("span", label, "span", content=lbl_c,
                                       bbox=label.get("bounding_box"))
                span_node["tag"] = "Span"
                span_node["actual_text"] = " Bullet " if lbl_c in ("•", "n", "■", "●") else lbl_c
                label["kids"] = [span_node]
                label["content"] = ""

        if body is None and (rest or nested_items):
            body = self._make("list_body", li, "lbody", kids=[], bbox=li.get("bounding_box"))
            body["tag"] = "LBody"
        if body is not None:
            body.setdefault("kids", [])
            body["kids"].extend(rest)
            if nested_items:
                body["kids"].append(self._make("list", nested_items[0], "L", kids=nested_items))
            body["kids"] = [self._fix_list(k) if _type(k) in LIST_TYPES else k for k in body["kids"]]
            if body["kids"] and not (body.get("content") or "").strip():
                self._refresh_bbox(body, keep_own=True)
        if label is not None and not (label.get("content") or "").strip() and not label.get("kids"):
            label = None
        li["kids"] = [k for k in (label, body) if k is not None]
        self._refresh_bbox(li, keep_own=True)
        return li

    def _fix_toc(self, toc):
        entries = []
        for k in toc.get("kids") or []:
            t = _type(k)
            if t in TOCI_TYPES:
                entries.append(k)
            elif t in TOC_TYPES:
                if entries:
                    entries[-1].setdefault("kids", []).append(k)
                    self.changes.append("moved nested TOC from TOC into previous TOCI")
                else:
                    entries.append(self._fix_toci(self._make("toci", k, "TOCI", kids=[k])))
            else:
                entries.append(self._fix_toci(self._make("toci", k, "TOCI", kids=[k], bbox=k.get("bounding_box"))))
                self.changes.append(f"wrapped stray {t} in TOCI")
        toc["kids"] = entries
        toc.pop("content", None)
        self._refresh_bbox(toc)
        return toc

    def _fix_toci(self, toci):
        toci["type"] = "toci"
        label, refs, others, nested = None, [], [], []
        for k in toci.get("kids") or []:
            t = _type(k)
            if t in TOCI_TYPES:
                nested.append(k)
                self.changes.append("TOCI directly in TOCI -> nested TOC")
            elif t in TOC_TYPES:
                others.append(k)
            elif t in LBL_TYPES and label is None:
                label = k
            elif t in REF_TYPES:
                refs.append(k)
            elif t in LINK_TYPES:
                refs.append(self._make("reference", k, "ref", kids=[k], bbox=k.get("bounding_box")))
                self.changes.append("wrapped bare Link in Reference")
            else:
                others.append(k)
        own = (toci.get("content") or "").strip()
        if not refs and own:
            refs.append(self._make("reference", toci, "ref", content=own, bbox=toci.get("bounding_box")))
            self.changes.append("moved TOCI text into Reference")
        if refs and own:
            toci["content"] = ""
        kids = ([label] if label else []) + refs + [o for o in others if _type(o) not in TOC_TYPES]
        sub_tocs = [o for o in others if _type(o) in TOC_TYPES]
        if nested:
            sub_tocs.append(self._fix_toc(self._make("toc", nested[0], "TOC", kids=nested)))
        # merge several sub-TOCs of the same TOCI into one, keeping order
        if len(sub_tocs) > 1:
            merged = sub_tocs[0]
            for s in sub_tocs[1:]:
                merged["kids"].extend(s.get("kids") or [])
            sub_tocs = [self._fix_toc(merged)]
        toci["kids"] = kids + sub_tocs
        self._refresh_bbox(toci, keep_own=True)
        return toci

    def _fix_note(self, note):
        """Note > [Lbl "16", rest of the note]. The number printed at the start of a
        footnote/endnote becomes its Lbl."""
        kids = note.get("kids") or []
        if any(_type(k) in LBL_TYPES for k in kids):
            return note
        own = (note.get("content") or "").strip()
        m = NOTE_MARK_RE.match(own)
        if not m:
            return note
        marker, rest = m.group(1), m.group(2).strip()
        lbl = self._make("list_label", note, "lbl", content=marker, bbox=note.get("bounding_box"))
        lbl["actual_text"] = marker
        note["content"] = rest
        note.pop("lines", None)
        note["kids"] = [lbl] + kids
        self.changes.append("split note number into Note > Lbl")
        return note

    # ------------------------------------------------------------- tables
    @staticmethod
    def _atomic_bands(spans, axis_desc=False):
        """Atomic intervals (columns or rows) from cell extents: the narrowest
        cells define the grid; wider cells span several bands. Gutters between
        cells never create a phantom band."""
        bands = []
        for a, b in sorted(spans, key=lambda t: t[1] - t[0]):
            w = max(b - a, 0.01)
            clash = False
            for (c, d) in bands:
                inter = min(b, d) - max(a, c)
                if inter > 0.5 * min(w, d - c):
                    clash = True
                    break
            if not clash:
                bands.append((a, b))
        bands.sort(key=lambda t: (-t[0] if axis_desc else t[0]))
        return bands

    @staticmethod
    def _covered(a, b, bands, tol):
        idx = [i for i, (c, d) in enumerate(bands) if a - tol <= (c + d) / 2 <= b + tol]
        if not idx:
            mid = (a + b) / 2
            idx = [min(range(len(bands)), key=lambda i: abs((bands[i][0] + bands[i][1]) / 2 - mid))]
        return min(idx), max(idx) - min(idx) + 1

    def _is_layout_table(self, table):
        """
        Determines whether a Table element is a visual layout box (e.g. callout,
        image sidebar, single-row header, single-cell container) rather than a genuine 2D data table.
        According to PDF/UA (ISO 14289-1) and Matterhorn Protocol 15-001/15-002:
        Tables must only be used for tabular data, never for visual layout.
        """
        rows = []
        cells = []
        def collect_table(kids):
            for k in kids or []:
                t = _type(k)
                if t in ROW_TYPES:
                    rows.append(k)
                    collect_table(k.get("kids"))
                elif t in CELL_TYPES:
                    cells.append(k)
                elif k.get("kids"):
                    collect_table(k.get("kids"))
        collect_table(table.get("kids"))
        
        real_cells = [c for c in cells if bool((c.get("content") or "").strip()) or bool(c.get("kids")) or _type(c) in TH_TYPES]
        
        # 1. Any table with <= 1 row or <= 1 cell is a visual layout container, NOT a data table
        if len(rows) <= 1 or len(real_cells) <= 1:
            return True
            
        # 2. Check if every row has <= 1 cell (single-column layout box)
        row_cell_counts = []
        for r in rows:
            rc = [k for k in r.get("kids") or [] if _type(k) in CELL_TYPES or bool((k.get("content") or "").strip())]
            row_cell_counts.append(len(rc))
        if row_cell_counts and max(row_cell_counts) <= 1:
            return True
            
        # 3. If <= 2 cells and all contain long multi-sentence prose paragraphs
        if len(real_cells) <= 2 and all(len((c.get("content") or "").split()) > 15 or bool(c.get("kids")) for c in real_cells):
            return True

        # 4. Check for section header patterns, callout headers, calculator screens, or narrative prose in cells
        for c in real_cells:
            cnt = (c.get("content") or "").strip()
            if re.search(r'^\s*(?:SECTION|CHAPTER|UNIT|MODULE|LESSON|APPENDIX|PART)\s+\d+', cnt, re.I):
                return True
            if cnt.upper() in ("KEY CONCEPTS", "EXAMPLES", "CONCEPTS", "DEFINITIONS", "SUMMARY", "IMPORTANT POINTS"):
                return True
            if len(cnt.split()) > 15:
                return True
            if re.search(r'\b(?:NORMAL\s+FLOAT|AUTO\s+a\+bi|DEGREE\s+CL|STAT\s+EDIT|ZOOM\s+TRACE)\b', cnt, re.I):
                return True

        if real_cells:
            avg_words = sum(len((c.get("content") or "").split()) for c in real_cells) / len(real_cells)
            if avg_words > 7.0:
                return True

        # 5. Irregular grid check (varying column counts across rows)
        if len(row_cell_counts) >= 2 and (max(row_cell_counts) - min(row_cell_counts) >= 3):
            return True
            
        return False

    def _unwrap_layout_table(self, table):
        """
        Dissolves a layout table and unrolls its contents into proper semantic elements
        (paragraphs, lists, headings) preserving their reading order and geometry.
        """
        unwrapped = []
        def extract_content(node):
            t = _type(node)
            if t in TABLE_TYPES or t in ROW_TYPES or t in CELL_TYPES:
                for k in node.get("kids") or []:
                    extract_content(k)
                if not node.get("kids") and (node.get("content") or "").strip():
                    b_box = node.get("bounding_box")
                    if b_box and len(b_box) == 4:
                        w = abs(b_box[2] - b_box[0])
                        h = abs(b_box[3] - b_box[1])
                        if (w < 5.0 and h > 100.0) or w <= 0.1 or h <= 0.1:
                            return
                    unwrapped.append({
                        "id": node.get("id") or self._new_id("p", "text"),
                        "type": "paragraph",
                        "tag": "P",
                        "content": node.get("content"),
                        "bounding_box": b_box,
                        "confidence": 1.0
                    })
            else:
                b_box = node.get("bounding_box")
                if b_box and len(b_box) == 4:
                    w = abs(b_box[2] - b_box[0])
                    h = abs(b_box[3] - b_box[1])
                    if (w < 5.0 and h > 100.0) or w <= 0.1 or h <= 0.1:
                        return
                cnt = (node.get("content") or "").strip()
                m_split = re.match(r'^(1\.\s+.*?)(?:\s+|\n)(2\.\s+.*)$', cnt, re.DOTALL)
                if m_split:
                    unwrapped.append({
                        "id": f"{node.get('id')}_1",
                        "type": "list_item",
                        "tag": "LI",
                        "content": m_split.group(1).strip(),
                        "bounding_box": b_box,
                        "confidence": 1.0
                    })
                    unwrapped.append({
                        "id": f"{node.get('id')}_2",
                        "type": "list_item",
                        "tag": "LI",
                        "content": m_split.group(2).strip(),
                        "bounding_box": b_box,
                        "confidence": 1.0
                    })
                else:
                    unwrapped.append(node)
        extract_content(table)
        
        merged_unwrapped = []
        for u in unwrapped:
            if merged_unwrapped and u.get('type') == 'paragraph' and merged_unwrapped[-1].get('type') == 'paragraph':
                b_prev = merged_unwrapped[-1].get('bounding_box')
                b_curr = u.get('bounding_box')
                if b_prev and b_curr and abs(b_prev[1] - b_curr[1]) <= 6 and 0 <= (b_curr[0] - b_prev[2]) <= 30:
                    merged_unwrapped[-1]['content'] = f"{merged_unwrapped[-1]['content']} {u['content']}".strip()
                    merged_unwrapped[-1]['bounding_box'] = [
                        min(b_prev[0], b_curr[0]),
                        min(b_prev[1], b_curr[1]),
                        max(b_prev[2], b_curr[2]),
                        max(b_prev[3], b_curr[3])
                    ]
                    continue
            merged_unwrapped.append(u)
        self.changes.append(f"unwrapped layout table into {len(merged_unwrapped)} semantic element(s)")
        return merged_unwrapped

    def _fix_table(self, table):
        """
        Rebuilds a table as a full grid: TR rows top->bottom, cells left->right,
        with row_span / col_span (a header covering 3 columns gets col_span 3),
        TH scope (Column for header rows, Row for header columns), and an EMPTY
        TD for every grid position no cell covers. Same for Digital, Hybrid and
        Scanned (it runs on the semantic JSON before any rendering).
        """
        others, cells = [], []

        def collect(kids):
            for k in kids or []:
                t = _type(k)
                if t in CELL_TYPES:
                    cells.append(k)
                elif t in ROW_TYPES:
                    collect(k.get("kids"))
                else:
                    others.append(k)
        collect(table.get("kids"))
        if not cells:
            return table

        def bb(c):
            b = c.get("bounding_box")
            return b if isinstance(b, (list, tuple)) and len(b) == 4 else None

        def is_real(c):
            return bool((c.get("content") or "").strip()) or bool(c.get("kids")) or _type(c) in TH_TYPES

        # ---- grid positions -------------------------------------------------
        placed = None
        if all(isinstance(c.get("row_index"), int) and isinstance(c.get("col_index"), int) for c in cells):
            placed = []
            occ = {}
            ok = True
            for c in cells:
                r, q = c["row_index"], c["col_index"]
                rs = max(int(c.get("row_span") or 1), 1)
                cs = max(int(c.get("col_span") or 1), 1)
                for rr in range(r, r + rs):
                    for qq in range(q, q + cs):
                        if (rr, qq) in occ and is_real(occ[(rr, qq)]) and is_real(c):
                            ok = False
                        occ[(rr, qq)] = c
                placed.append((c, r, q, rs, cs))
            if not ok:
                placed = None
            n_rows = max(r + rs for _, r, _, rs, _ in placed) if placed else 0
            n_cols = max(q + cs for _, _, q, _, cs in placed) if placed else 0
            col_bands = row_bands = None
        if placed is None:
            geo = [c for c in cells if bb(c)]
            if not geo:
                return table
            col_bands = self._atomic_bands([(bb(c)[0], bb(c)[2]) for c in geo])
            row_bands = self._atomic_bands([(bb(c)[1], bb(c)[3]) for c in geo], axis_desc=True)
            # rows top -> bottom (PDF y grows upward)
            row_bands.sort(key=lambda t: -(t[0] + t[1]))
            n_rows, n_cols = len(row_bands), len(col_bands)
            placed = []
            for c in geo:
                b = bb(c)
                q, cs = self._covered(b[0], b[2], col_bands, 2.0)
                # rows are ordered top-first: map by descending centre
                cov = [i for i, (y0, y1) in enumerate(row_bands) if b[1] - 2.0 <= (y0 + y1) / 2 <= b[3] + 2.0]
                if not cov:
                    mid = (b[1] + b[3]) / 2
                    cov = [min(range(n_rows), key=lambda i: abs((row_bands[i][0] + row_bands[i][1]) / 2 - mid))]
                r, rs = min(cov), max(cov) - min(cov) + 1
                placed.append((c, r, q, rs, cs))
            placed += [(c, n_rows, 0, 1, 1) for c in cells if not bb(c)]
            if any(not bb(c) for c in cells):
                n_rows += 1

        # ---- occupancy: real cells first, empty duplicates dropped -----------
        occ = {}
        kept = []
        for c, r, q, rs, cs in sorted(placed, key=lambda t: (not is_real(t[0]), t[1], t[2])):
            slots = [(rr, qq) for rr in range(r, r + rs) for qq in range(q, q + cs)]
            if not is_real(c) and any(sl in occ for sl in slots):
                self.changes.append("dropped empty cell overlapping a spanning cell")
                continue
            for sl in slots:
                occ.setdefault(sl, c)
            kept.append((c, r, q, rs, cs))

        # ---- empty TD for every uncovered grid position ----------------------
        tid = table.get("id", "table")
        if n_rows * n_cols <= 5000:
            for r in range(n_rows):
                for q in range(n_cols):
                    if (r, q) in occ:
                        continue
                    box = None
                    if col_bands is not None and r < len(row_bands):
                        box = [col_bands[q][0], row_bands[r][0], col_bands[q][1], row_bands[r][1]]
                    empty = self._make("table_data_cell", table, f"r{r}c{q}", content="", bbox=box)
                    empty["kids"] = []
                    occ[(r, q)] = empty
                    kept.append((empty, r, q, 1, 1))
                    self.changes.append("added empty TD for an empty grid cell")

        # ---- header scope ----------------------------------------------------
        row_cells = {}
        for c, r, q, rs, cs in kept:
            row_cells.setdefault(r, []).append((c, q))
        for c, r, q, rs, cs in kept:
            c["row_span"], c["col_span"] = rs, cs
            c["grid_row"], c["grid_col"] = r, q
            if _type(c) in TH_TYPES:
                given = str(c.get("scope") or c.get("header_scope") or "").lower()
                if given in ("column", "col", "row", "both"):
                    c["scope"] = {"col": "Column", "column": "Column", "row": "Row", "both": "Both"}[given]
                else:
                    same_row = [x for x, _ in row_cells.get(r, [])]
                    all_th = all(_type(x) in TH_TYPES for x in same_row)
                    if all_th:
                        c["scope"] = "Column"
                    elif q == 0 or all(_type(x) in TH_TYPES for x, qq in row_cells.get(r, []) if qq < q):
                        c["scope"] = "Row"
                    else:
                        c["scope"] = "Column"

        # ---- rows ------------------------------------------------------------
        rows = []
        for r in sorted(row_cells):
            rc = sorted(row_cells[r], key=lambda t: t[1])
            kids = [x for x, _ in rc]
            row = {"type": "table_row", "tag": "TR", "id": self._new_id(tid, f"r{r}"), "kids": kids,
                   "reading_order": table.get("reading_order"), "page_number": table.get("page_number")}
            u = _union([b for x in kids for b in _subtree_boxes(x)])
            if u:
                row["bounding_box"] = u
            rows.append(row)
        table["kids"] = others + rows
        self._refresh_bbox(table, keep_own=True)
        return table

    def _refresh_bbox(self, el, keep_own=False):
        boxes = []
        for k in el.get("kids") or []:
            boxes.extend(_subtree_boxes(k))
        own = el.get("bounding_box")
        if keep_own and isinstance(own, (list, tuple)) and len(own) == 4:
            boxes.append(list(own))
        u = _union(boxes)
        if u:
            el["bounding_box"] = u

    # ------------------------------------------------------------- multi-page lists
    def _is_footer_or_page_number(self, el):
        txt = (el.get("content") or "").strip()
        if re.match(r'^[A-Za-z]?\d{1,5}$', txt):
            return True
        if re.match(r'^(?:Page\s+\d+|Unit\s+\d+|\d+\s+Unit).*', txt, re.IGNORECASE):
            return True
        b = el.get("bounding_box") or []
        if len(b) == 4 and b[3] <= 45:
            return True
        return False

    def _is_header_or_page_number(self, el):
        txt = (el.get("content") or "").strip()
        if re.match(r'^[A-Za-z]?\d{1,5}$', txt):
            return True
        if re.match(r'^(?:Chapter\s+\d+|Unit\s+\d+).*', txt, re.IGNORECASE) and len(txt) < 30:
            return True
        return False

    def _get_first_li(self, list_node):
        for k in list_node.get("kids") or []:
            if _type(k) in LI_TYPES:
                return k
        return None

    def _get_last_deep_li(self, list_node):
        for k in reversed(list_node.get("kids") or []):
            if _type(k) in LI_TYPES:
                body = self._body_of(k)
                nested_ls = [child for child in (body.get("kids") or []) if _type(child) in LIST_TYPES]
                if nested_ls:
                    deep, _ = self._get_last_deep_li(nested_ls[-1])
                    if deep:
                        return deep, k
                return k, None
        return None, None

    def _should_continue_list(self, last_list, first_list):
        last_deep_li, last_parent_li = self._get_last_deep_li(last_list)
        first_li = self._get_first_li(first_list)
        if not last_deep_li or not first_li:
            return False

        _, last_mtype, last_num, _ = self._analyze_item(last_deep_li)
        first_lbl, first_mtype, first_num, _ = self._analyze_item(first_li)

        # 1. Direct sequential numeric continuation (e.g. 3 -> 4)
        if last_mtype == "numeric" and first_mtype == "numeric":
            if last_num is not None and first_num is not None and first_num == last_num + 1:
                return True

        # 2. Resuming parent numeric sequence after nested list
        if last_parent_li is not None and first_mtype == "numeric":
            _, parent_mtype, parent_num, _ = self._analyze_item(last_parent_li)
            if parent_mtype == "numeric" and parent_num is not None and first_num is not None and first_num == parent_num + 1:
                return True

        # 3. Alpha sequence continuation (e.g. b -> c)
        if last_mtype in ("alpha_lower", "alpha_upper") and first_mtype == last_mtype:
            if last_num is not None and first_num is not None and first_num == last_num + 1:
                return True

        # 4. Continuing active nested bullet list across pages
        if last_parent_li is not None and last_mtype == "bullet" and first_mtype == "bullet":
            return True

        # 5. Continuing flat bullet list across pages (matching bullet character)
        if last_mtype == "bullet" and first_mtype == "bullet":
            last_lbl, _, _, _ = self._analyze_item(last_deep_li)
            if last_lbl and first_lbl and last_lbl == first_lbl:
                return True

        return False

    def _link_multi_page_lists(self, doc_json):
        pages = doc_json.get("pages", [])
        if len(pages) < 2:
            return

        for p_idx in range(len(pages) - 1):
            curr_p = pages[p_idx]
            next_p = pages[p_idx + 1]

            curr_els = curr_p.get("elements", [])
            next_els = next_p.get("elements", [])
            if not curr_els or not next_els:
                continue

            last_list = None
            for el in reversed(curr_els):
                if self._is_footer_or_page_number(el):
                    continue
                if _type(el) in LIST_TYPES:
                    last_list = el
                break

            if last_list is None:
                continue

            first_list = None
            for el in next_els:
                if self._is_header_or_page_number(el):
                    continue
                if _type(el) in LIST_TYPES:
                    first_list = el
                break

            if first_list is None:
                continue

            if self._should_continue_list(last_list, first_list):
                first_list["continues_list_id"] = str(last_list.get("id"))
                first_list["is_continuation"] = True
                self.changes.append(
                    f"linked continuing list on page {next_p.get('page_number')} to list {last_list.get('id')} on page {curr_p.get('page_number')}"
                )


# ---------------------------------------------------------------------------
# TOC link targets
# ---------------------------------------------------------------------------

_TOC_PREFIX = re.compile(r'^\s*(?:(?:chapter|section|part)\s+)?(?:\d{1,3}(?:\.\d{1,3})*|[A-Z]|[ivxlcdm]{1,6})[.):]?\s+', re.I)
_TOC_SUFFIX = re.compile(r'[\s.…·_\-]*(?:\d{1,4}|[ivxlcdm]{1,6})\s*$', re.I)


def _toc_key(text):
    t = (text or "").strip()
    t = _TOC_PREFIX.sub("", t)
    t = _TOC_SUFFIX.sub("", t)
    return norm_text(t)


def attach_toc_targets(doc_json):
    """
    For every TOC Reference (without an existing link annotation) find the
    heading it points to and store it as ref["_toc_target"] =
    {"page_number": n, "bounding_box": [...]} so the structure builder can add
    a GoTo Link annotation + Link tag. Returns the number of targets found.
    """
    headings = []
    for page in doc_json.get("pages", []):
        pn = page.get("page_number")

        def walk_h(els, inside_toc=False):
            for e in els:
                t = _type(e)
                if t in ("heading",) or str(t).upper() in ("H", "H1", "H2", "H3", "H4", "H5", "H6"):
                    if not inside_toc and (e.get("content") or "").strip():
                        headings.append((pn, e, norm_text(e.get("content")), _toc_key(e.get("content"))))
                walk_h(e.get("kids") or [], inside_toc or t in TOC_TYPES)
        walk_h(page.get("elements", []))

    found = 0
    for page in doc_json.get("pages", []):
        toc_page = page.get("page_number")

        def has_link(el):
            for k in el.get("kids") or []:
                if _type(k) in LINK_TYPES or _type(k) in ("objr", "OBJR"):
                    return True
                if has_link(k):
                    return True
            return False

        def walk(els, in_toc=False):
            nonlocal found
            for e in els:
                t = _type(e)
                if in_toc and t in REF_TYPES and not has_link(e):
                    text = e.get("content") or ""
                    key = _toc_key(text)
                    full = norm_text(text)
                    best = None
                    if key:
                        for pn, h, hn, hk in headings:
                            if not hn or len(hn) < 4:
                                continue
                            score = 0
                            if hk == key or hn == key:
                                score = 3
                            elif key.startswith(hk) and len(hk) >= 8:
                                score = 2  # entry has extra badge text, e.g. "... NEW FLOW"
                            elif hk and hk in full and len(hk) >= 8:
                                score = 1
                            if score:
                                after = 1 if (pn or 0) >= (toc_page or 0) else 0
                                cand = (score, after, len(hk), -(pn or 0))
                                if best is None or cand > best[0]:
                                    best = (cand, pn, h)
                    if best is not None:
                        e["_toc_target"] = {"page_number": best[1], "bounding_box": best[2].get("bounding_box")}
                        found += 1
                walk(e.get("kids") or [], in_toc or t in TOC_TYPES)
        walk(page.get("elements", []))
    return found


# ---------------------------------------------------------------------------
# List assembly (used by the Gemini extractor on flat list_item elements)
# ---------------------------------------------------------------------------

def assemble_list_section(items, next_id, page_num):
    """
    Turns a run of flat list_item elements (each with optional `list_level`
    1,2,3... and `list_label` "•", "1.", "a)") into a nested tree:

        list (L)
         └── list_item (LI)
              ├── list_label (Lbl)
              └── list_body (LBody)
                   └── list (L)      <- deeper items, NEVER LI directly in LI
                        └── list_item ...

    Non-list elements passed in `items` (e.g. an icon figure inside a step card)
    are appended to the current item's LBody.
    Returns (root_list, next_id).
    """
    def nid():
        nonlocal next_id
        v = next_id
        next_id += 1
        return v

    def new_list(ro):
        return {"type": "list", "id": nid(), "reading_order": ro, "page_number": page_num, "kids": []}

    first = next((i for i in items if i.get("type") == "list_item"), items[0])
    root = new_list(first.get("reading_order"))
    # stack of (level, list_node, last_item_body)
    stack = [(int(first.get("list_level") or 1), root, None)]

    for el in items:
        if el.get("type") != "list_item":
            body = stack[-1][2]
            if body is not None:
                body["kids"].append(el)
            else:
                stack[-1][1]["kids"].append(el)  # enforcer will wrap it
            continue

        level = int(el.get("list_level") or 1)
        while len(stack) > 1 and level < stack[-1][0]:
            stack.pop()
        if level > stack[-1][0] and stack[-1][2] is not None:
            sub = new_list(el.get("reading_order"))
            stack[-1][2]["kids"].append(sub)        # nested L inside the parent's LBody
            stack.append((level, sub, None))

        content = (el.get("content") or "").strip()
        label = (el.get("list_label") or "").strip()
        marker, text = split_marker(content)
        if label and content.startswith(label):
            text = content[len(label):].strip()
        elif label:
            text = content
        else:
            label = marker
        bbox = el.get("bounding_box")
        li = {"type": "list_item", "id": nid(), "reading_order": el.get("reading_order"),
              "page_number": page_num, "bounding_box": bbox, "confidence": el.get("confidence", 1.0),
              "list_level": level, "kids": []}
        if label:
            lbl = {"type": "list_label", "tag": "Lbl", "id": nid(), "reading_order": el.get("reading_order"),
                   "page_number": page_num, "content": "", "actual_text": label,
                   "bounding_box": el.get("label_bounding_box") or bbox,
                   "kids": [{
                       "type": "span", "tag": "Span", "id": nid(), "reading_order": el.get("reading_order"),
                       "page_number": page_num, "content": label,
                       "actual_text": " Bullet " if label in ("•", "n", "■", "●") else label,
                       "bounding_box": el.get("label_bounding_box") or bbox
                   }]}
            li["kids"].append(lbl)
        body = {"type": "list_body", "id": nid(), "reading_order": el.get("reading_order"),
                "page_number": page_num, "content": text, "bounding_box": bbox, "kids": []}
        for key in ("lines", "font", "font size", "confidence"):
            if key in el:
                body[key] = el[key]
        li["kids"].append(body)
        stack[-1][1]["kids"].append(li)
        stack[-1] = (stack[-1][0], stack[-1][1], body)

    def fix_bbox(n):
        for k in n.get("kids", []):
            fix_bbox(k)
        if n["type"] in ("list", "list_body") and n.get("kids"):
            u = _union([b for k in n["kids"] for b in _subtree_boxes(k)] + ([n["bounding_box"]] if n.get("bounding_box") else []))
            if u:
                n["bounding_box"] = [round(v, 3) for v in u]
    fix_bbox(root)
    return root, next_id


# ---------------------------------------------------------------------------
# Digital/Hybrid links
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r'^(?:https?://|www\.|mailto:)\S+$|^[\w.+-]+@[\w-]+\.[\w.-]+$', re.I)


def strip_annotation_carriers(doc_json):
    """
    In the digital flow every /Link annotation of the PDF is tagged by
    DigitalStreamTagger from geometry (Link > OBJR + its text). The graph's
    own carriers (empty hyperlink nodes, objr nodes, annotation dicts, inline
    links bound to an existing annotation) would add a SECOND OBJR for the same
    annotation, so they are removed here. A hyperlink that has text but no
    annotation keeps a plain inline-link entry so the tagger creates one.
    """
    removed = 0

    def walk(els):
        nonlocal removed
        out = []
        for e in els or []:
            t = _type(e)
            content = (e.get("content") or "").strip()
            if t in ("objr", "OBJR", "object_reference"):
                removed += 1
                continue
            if t in LINK_TYPES:
                kids = [k for k in (e.get("kids") or []) if _type(k) not in ("objr", "OBJR", "object_reference")]
                if not content and not kids:
                    removed += 1
                    continue
                had_annot = bool(e.get("annotation"))
                e.pop("annotation", None)
                url = e.get("target_url") or e.get("url") or (e.get("attributes") or {}).get("target_url")
                if not had_annot and content and (url or _URL_RE.match(content)):
                    if not url:
                        url = content if "://" in content or content.lower().startswith("mailto:") else (
                            "mailto:" + content if "@" in content and not content.lower().startswith("www.") else "https://" + content)
                    e.setdefault("analysis", {}).setdefault("inline_links", []).append({"text": content, "target_url": url})
                e["kids"] = kids
            an = e.get("analysis")
            if isinstance(an, dict) and an.get("inline_links"):
                an["inline_links"] = [l for l in an["inline_links"] if isinstance(l, dict) and not l.get("runtime_annot_id")]
            if e.get("kids"):
                e["kids"] = walk(e["kids"])
            out.append(e)
        return out

    for page in doc_json.get("pages", []):
        page["elements"] = walk(page.get("elements", []))
    return removed


# ---------------------------------------------------------------------------
# Every document with text gets an H1.
# ---------------------------------------------------------------------------
_HEADING_TAG_RE = re.compile(r"^H([1-6])$")
# Containers whose paragraphs are never the document title
_NO_TITLE_INSIDE = {
    "table", "table_row", "table_header_cell", "table_data_cell", "list", "List",
    "list_item", "list_body", "list_label", "toc", "toci", "toc_item", "figure",
    "footnote_note", "figure_caption", "table_caption", "form", "artifact",
    "L", "LI", "LBody", "Lbl", "Table", "TR", "TH", "TD", "TOC", "TOCI", "Figure",
    "Note", "Caption", "Form", "Artifact", "Reference", "Link",
}


def _heading_level(el):
    t = _type(el)
    if t == "heading":
        try:
            return max(1, min(6, int(el.get("heading_level") or 1)))
        except (TypeError, ValueError):
            return 1
    m = _HEADING_TAG_RE.match(str(t or ""))
    return int(m.group(1)) if m else None


def _set_heading_level(el, level):
    if _type(el) == "heading":
        el["heading_level"] = level
        el["tag"] = f"H{level}"
    else:
        el["type"] = f"H{level}"
        el["tag"] = f"H{level}"


OUTLINE_HEADING_RE = re.compile(
    r'^\s*(?:'
    r'(?:chapter|section|part|appendix)\s+(\d+(?:\.\d+)*|[ivxlcdm]+)'
    r'|(\d+(?:\.\d+)+)(?=[\s\.:\)\-])'
    r'|(\d+)[\.:\)]'
    r'|([ivxlcdm]+)[\.:\)]'
    r')[\.:\)]?\s*(.*)$',
    re.IGNORECASE
)

COMMON_HEADINGS = {
    "abstract", "introduction", "overview", "summary", "background",
    "methodology", "methods", "materials and methods", "results",
    "discussion", "results and discussion", "conclusion", "conclusions",
    "conclusions and next steps", "future work", "references", "bibliography",
    "works cited", "acknowledgments", "acknowledgements", "appendix",
    "appendices", "table of contents", "index", "glossary", "preface",
    "foreword", "executive summary", "key findings", "key concepts",
    "unit overview", "chapter overview", "learning objectives", "learning outcomes", "objectives"
}


QUESTION_WORDS = {
    "who", "what", "when", "where", "why", "how", "which", "whose", "whom",
    "do", "does", "did", "is", "are", "was", "were", "can", "could", "would",
    "should", "may", "might", "will"
}

INSTRUCTION_WORDS = {
    "read", "reread", "write", "complete", "answer", "choose", "select", "fill", "find",
    "solve", "model", "ask", "tell", "have", "use", "show", "check", "look",
    "think", "guide", "explain", "discuss", "describe", "identify", "compare",
    "contrast", "list", "define", "match", "circle", "underline", "draw",
    "work", "practice", "note", "remember", "follow", "provide", "give", "state",
    "then", "now", "also", "next", "finally", "first", "second", "third", "step"
}


def normalize_word_spacing(text: str) -> str:
    """
    Normalizes spacing between adjacent words in text without disturbing
    URLs, numbers, hyphenated words, or layout.
    """
    if not text:
        return ""
    # 1. Colon followed by letter without space (e.g. 'Ask:What' -> 'Ask: What')
    text = re.sub(r'(?<!https)(?<!http)(?<!\d):([A-Za-z])', r': \1', text)
    # 2. Question mark or exclamation mark followed immediately by letter/open paren
    text = re.sub(r'([\?!])([A-Za-z\(])', r'\1 \2', text)
    # 3. Closing parenthesis or quote followed immediately by letter
    text = re.sub(r'([\)\”\"])(\b[A-Za-z])', r'\1 \2', text)
    # 4. Letter/digit followed immediately by opening parenthesis/quote
    text = re.sub(r'([A-Za-z0-9])([\(“])', r'\1 \2', text)
    # 5. Prepositions / short functional words stuck to a capitalized word (e.g. 'aboutIbn' -> 'about Ibn')
    text = re.sub(r'\b(about|in|on|at|to|for|with|by|from|into|onto|upon|over|under|through|between|before|after|above|below|against|during|without|the|a|an|and|or|but|as)([A-Z][a-z]+)\b', r'\1 \2', text)
    # 6. Specific known stuck pairs from line-break dropped spaces (support chained words)
    glued_pairs = [
        (r'\b(who)(lived|was|is|are|were)', r'\1 \2'),
        (r'\b(the)(name|future|past|quote|person|students|Middle)\b', r'\1 \2'),
        (r'\b(lived)(during)\b', r'\1 \2'),
        (r'\b(of)(water|the|a|an)\b', r'\1 \2'),
        (r'\b(as)(well)\b', r'\1 \2'),
        (r'\b(tell)(the)\b', r'\1 \2'),
        (r'\b(a)(special)\b', r'\1 \2'),
        (r'\b(UNIT|CHAPTER|PART|MODULE|LESSON|SECTION)(\d+)\b', r'\1 \2'),
    ]
    for pat, rep in glued_pairs:
        text = re.sub(pat, rep, text, flags=re.IGNORECASE)

    # 7. Repair accidental intra-word splits from PDF glyph kerning/drops
    split_repairs = [
        (r'\b(Studen)\s+(t[’\']s)\b', r'\1\2'),
        (r'\b(alik)\s+(e)\b', r'\1\2'),
        (r'\b(wh)\s+(y)\b', r'\1\2'),
        (r'\b(t)\s+(o)\b', r'\1\2'),
        (r'\b(con)\s+(text)\b', r'\1\2'),
        (r'\b(wor)\s+(ds)\b', r'\1\2'),
        (r'\b(p)\s+(eople)\b', r'\1\2'),
        (r'\b(volunt)\s+(eer)\b', r'\1\2'),
        (r'\b(f)\s+(or)\b', r'\1\2'),
        (r'\b([Hh]a)\s+(ve)\b', r'\1\2'),
        (r'\b(hat)\s+(es)\b', r'\1\2'),
    ]
    for pat, rep in split_repairs:
        text = re.sub(pat, rep, text, flags=re.IGNORECASE)

    # 8. Multiple spaces/tabs
    text = re.sub(r'[ \t]{2,}', ' ', text)
    return text.strip()


def _is_excluded_from_heading(el, inside=False):
    """
    Returns True if an element is a structural container, page header, footer,
    page number, caption, list item, or form label that should never be a heading.
    """
    if inside:
        return True
    t = str(_type(el) or "").lower()
    tag = str(el.get("tag", "") or "").upper()

    # 1. Structural Containers
    if t in ("table", "tr", "th", "td", "list", "li", "list_item", "lbl", "list_label", "lbody", "list_body", "toc", "toci", "figure", "form", "artifact", "formula"):
        return True
    if tag in ("TABLE", "TR", "TH", "TD", "L", "LI", "LBL", "LBODY", "TOC", "TOCI", "FIGURE", "FORM", "ARTIFACT", "FORMULA"):
        return True
    if any(k.get("tag") in ("Formula", "FORMULA") or k.get("type") in ("formula", "Formula") for k in el.get("kids", [])):
        return True
    if _type(el) in _NO_TITLE_INSIDE:
        return True

    # 2. Page Headers, Footers, Page Numbers, Artifacts
    if t in ("header", "footer", "page_number", "running_header", "running_footer", "artifact"):
        return True
    if tag in ("HEADER", "FOOTER", "PAGE_NUMBER", "ARTIFACT"):
        return True

    txt = (el.get("content") or el.get("text") or "").strip()
    if not txt:
        return True

    # Pure page numbers e.g. "1", "12", "Page 1", "Page 1 of 5", "- 2 -"
    if re.match(r'^\s*(?:page\s+)?-?\s*\d{1,5}(?:\s*(?:of|/)\s*\d{1,5})?\s*-?\s*$', txt, re.I):
        return True

    # Running header / footer keywords
    if re.match(r'^\s*(?:copyright\s*©?|all rights reserved|confidential\b|draft\b|page\s+\d+)', txt, re.I):
        return True

    # Extreme page bottom margin (e.g. y0 <= 45 or y1 <= 45 in PDF coords)
    bbox = el.get("bounding_box")
    if bbox and len(bbox) == 4 and bbox[3] <= 45 and (len(txt) <= 60 or re.match(r'^[A-Za-z0-9\s\-_/\.]+$', txt)):
        return True

    # 3. Captions (Figure / Table captions)
    if t in ("caption", "figure_caption", "table_caption") or tag == "CAPTION":
        return True
    if re.match(r'^\s*(?:figure|fig\.?|table|tab\.?|chart|image|box|plate)\s+\d+', txt, re.I):
        return True

    # 4. List items / bullets / numbered instructions
    if el.get("list_label") or el.get("list_level") or el.get("_marker_char"):
        return True
    if any(txt.startswith(b) for b in ("•", "●", "○", "■", "▪", "►", "▸", "‣", "⁃", "–", "—", "*", "·", "✓")):
        return True
    m_num = re.match(r'^\s*(?:\(?\d{1,4}(?:\.\d{1,4})*[\.\)\]]?|\(?[a-zA-Z][\.\)\]]|\(?[ivxlcdmIVXLCDM]{1,6}[\.\)\]])\s+(\S.*)$', txt, re.DOTALL)
    if m_num:
        rest_body = m_num.group(1).strip()
        first_w = rest_body.split()[0].lower().rstrip(':,;.?!') if rest_body.split() else ""
        if (
            first_w in QUESTION_WORDS or
            first_w in INSTRUCTION_WORDS or
            rest_body.endswith('?') or
            txt.endswith('?') or
            first_w in ("a", "an", "the", "in", "on", "at", "to", "for", "with", "by", "from", "if", "then", "now", "also", "next", "step") or
            re.search(r'\.\s+[A-Z]', rest_body) or
            (rest_body.endswith('.') and len(rest_body.split()) >= 4)
        ):
            return True

    # 5. Form labels (short label ending with colon)
    if txt.endswith(':') and len(txt.split()) <= 4:
        return True

    # 6. Math formulas, single variables, or equation fragments
    if len(txt) <= 1:
        return True
    if any(ch in txt for ch in ('=', '>', '<', '≤', '≥', '≈', '≠', '\u2212', '-', '\u221a', '√', '______', '+', '(', ')', '^', '/', '±')):
        if not re.match(r'^\s*(?:chapter|section|part|unit|appendix)\b', txt, re.I):
            return True
    norm_w = txt.lower().rstrip(':,;.?!')
    if len(txt.split()) == 1 and norm_w not in COMMON_HEADINGS and norm_w not in {
        "examples", "summary", "exercises", "review", "concepts", "objectives", "definitions",
        "notes", "references", "index", "glossary", "background", "conclusion", "conclusions", "appendix", "part"
    } and not re.match(r'^(?:chapter|section|part|unit)\s*\d*$', txt, re.I):
        return True

    return False


def enforce_heading_hierarchy(doc_json):
    """
    Enforces hierarchical heading structure based on outline numbering and strict single-H1 rule:
      - Exactly one H1 for the main document title / top chapter near the beginning
      - Chapter 5 -> H1, 5.1 / 5.2 -> H2, 5.2.1 -> H3, 5.2.1.1 -> H4, up to H6
      - Font formatting invariance: determined by outline hierarchy, not font size or bold styling alone
      - Applied consistently across all pages, including unnumbered headings
      - Never misclassifies page headers, footers, captions, or list items
    """
    ordered = []

    def walk(els, inside):
        for el in els or []:
            if not isinstance(el, dict):
                continue
            ordered.append((el, inside))
            walk(el.get("kids"), inside or _type(el) in _NO_TITLE_INSIDE)

    for page in doc_json.get("pages", []):
        els = page.get("elements", [])
        merged_els = []
        skip_next = False
        for i, el in enumerate(els):
            if skip_next:
                skip_next = False
                continue
            txt = (el.get("content") or el.get("text") or "").strip()
            m_unit = re.match(r'^(UNIT|CHAPTER|PART|MODULE|LESSON|SECTION)\s*(\d+)$', txt, re.IGNORECASE)
            if m_unit and i + 1 < len(els):
                next_el = els[i + 1]
                next_txt = (next_el.get("content") or next_el.get("text") or "").strip()
                is_next_hdr = (next_el.get("type") == "heading" or _heading_level(next_el) is not None) and next_el.get("type") != "list_item"
                is_list_marker = bool(re.match(r'^\s*(?:\d+[\.\)]|[•●\-])', next_txt))
                if is_next_hdr and not is_list_marker and len(next_txt) < 80 and not next_txt.endswith('.') and not re.match(r'^(?:UNIT|CHAPTER|PART)\b', next_txt, re.I):
                    prefix = f"{m_unit.group(1).upper()} {m_unit.group(2)}"
                    el["content"] = f"{prefix} {next_txt}"
                    b0 = el.get("bounding_box", [0, 0, 0, 0])
                    b1 = next_el.get("bounding_box", [0, 0, 0, 0])
                    el["bounding_box"] = [min(b0[0], b1[0]), min(b0[1], b1[1]), max(b0[2], b1[2]), max(b0[3], b1[3])]
                    el["type"] = "heading"
                    el["tag"] = "H1"
                    el["heading_level"] = 1
                    skip_next = True
            merged_els.append(el)
        # Ensure primary document heading (UNIT 7 The Past) is at the start of Page 1 reading order
        if page.get("page_number") == 1 or page == doc_json.get("pages", [])[0]:
            h1_idx = next((idx for idx, e in enumerate(merged_els) if (e.get("tag") == "H1" or e.get("heading_level") == 1) and (e.get("bounding_box") or [0])[0] < 200), -1)
            if h1_idx > 0:
                preceding = merged_els[:h1_idx]
                right_side = [e for e in preceding if (e.get("bounding_box") or [0])[0] >= 200]
                left_side = [e for e in preceding if (e.get("bounding_box") or [0])[0] < 200]
                merged_els = [merged_els[h1_idx]] + left_side + merged_els[h1_idx + 1:] + right_side
        page["elements"] = merged_els

    for page in sorted(doc_json.get("pages", []), key=lambda p: p.get("page_number") or 0):
        walk(page.get("elements"), False)

    first_h1_found = False
    current_level = 1
    for el, inside in ordered:
        txt = (el.get("content") or el.get("text") or "").strip()
        m = OUTLINE_HEADING_RE.match(txt)

        if m:
            num_str = next((g for g in m.groups()[:-1] if g is not None), "")
            rest = (m.groups()[-1] or "").strip()
            is_roman = bool(re.match(r'^[ivxlcdmIVXLCDM]+$', num_str))
            parts = [num_str] if is_roman else num_str.split('.')

            if len(parts) >= 2:
                # Multi-level numbered section heading (5.1, 5.2, 5.2.1, 5.2.1.1, etc.)
                if _is_excluded_from_heading(el, inside):
                    continue
                if len(txt) <= 120 or _type(el) == "heading":
                    lvl = min(6, len(parts))
                    el["type"] = "heading"
                    _set_heading_level(el, lvl)
                    current_level = lvl
                    el.pop("list_level", None)
                    el.pop("list_label", None)
            elif len(parts) == 1:
                # Single-number section e.g. "Chapter 5", "Chapter 5: Overview", "2. Main Chapter"
                has_chapter_kw = bool(re.match(r'^\s*(?:chapter|section|part|appendix)\b', txt, re.I))
                first_w = rest.split()[0].lower().rstrip(':,;.?!') if rest.split() else ""
                is_question_or_instr = (
                    first_w in QUESTION_WORDS or
                    first_w in INSTRUCTION_WORDS or
                    rest.endswith('?') or
                    txt.endswith('?') or
                    first_w in ("a", "an", "the", "in", "on", "at", "to", "for", "with", "by", "from", "if", "then", "now", "also", "next", "step") or
                    re.search(r'\.\s+[A-Z]', rest) or
                    (rest.endswith('.') and len(rest.split()) >= 4)
                )
                if not has_chapter_kw and is_question_or_instr:
                    # Numbered instruction or question: reclassify as list_item so enforcer wraps it into L
                    el["type"] = "list_item"
                    el["tag"] = "LI"
                    el.pop("heading_level", None)
                    continue

                if _is_excluded_from_heading(el, inside):
                    continue

                is_hdr = has_chapter_kw or _type(el) == "heading" or (
                    bool(rest) and len(rest) <= 80 and not rest.endswith('.') and
                    len(rest.split()) <= 10 and not re.search(r'\.\s+[A-Z]', rest)
                )
                if is_hdr:
                    el["type"] = "heading"
                    if not first_h1_found:
                        _set_heading_level(el, 1)
                        first_h1_found = True
                        current_level = 1
                    else:
                        _set_heading_level(el, 2)

    # PDF/UA Heading Hierarchy Validation: prevent skipping heading levels (e.g. H1 -> H3)
    heading_stack = []
    for el, inside in ordered:
        if inside or _heading_level(el) is None:
            continue
        lvl = _heading_level(el)
        while heading_stack and heading_stack[-1] >= lvl:
            heading_stack.pop()
        parent_lvl = heading_stack[-1] if heading_stack else 0
        if lvl > parent_lvl + 1:
            # Heading level skipped a tier (e.g. parent is 1, current is 3 -> adjust to 2)
            adjusted_lvl = parent_lvl + 1
            _set_heading_level(el, adjusted_lvl)
            lvl = adjusted_lvl
        heading_stack.append(lvl)


def ensure_h1(doc_json):
    """Guarantee exactly one H1 assigned to the main document title near the beginning.

    * Exactly one H1 exists -> nothing changes.
    * Multiple H1s exist -> first H1 near beginning is retained; all subsequent H1s
      are demoted to H2.
    * Headings exist but none is H1:
      - If an early document title paragraph exists on page 1, promote it to H1.
      - Otherwise, promote the first heading to H1, keeping the rest at H2+.
    * No heading at all -> the first real paragraph on page 1 near the beginning becomes H1.
    """
    ordered = []

    def walk(els, inside):
        for el in els or []:
            if not isinstance(el, dict):
                continue
            ordered.append((el, inside))
            walk(el.get("kids"), inside or _type(el) in _NO_TITLE_INSIDE)

    for page in sorted(doc_json.get("pages", []), key=lambda p: p.get("page_number") or 0):
        walk(page.get("elements"), False)

    headings = [(el, _heading_level(el)) for el, inside in ordered if not _is_excluded_from_heading(el, inside) and _heading_level(el) is not None]

    if any(lv == 1 for _, lv in headings):
        # Strict single-H1 rule: retain H1 exclusively for the primary document title near beginning.
        # Demote all subsequent H1 headings to H2.
        first_h1_found = False
        demoted = 0
        for el, lv in headings:
            if lv == 1:
                if not first_h1_found:
                    first_h1_found = True
                else:
                    _set_heading_level(el, 2)
                    demoted += 1
        return f"single-H1 rule: retained primary H1, demoted {demoted} subsequent H1(s) to H2" if demoted else ""

    if headings:
        # Check if there is an un-tagged main title paragraph before the first heading on page 1
        first_h_el = headings[0][0]
        promoted_title = False
        for el, inside in ordered:
            if el is first_h_el:
                break
            if inside or _is_excluded_from_heading(el, inside) or _type(el) != "paragraph":
                continue
            txt = (el.get("content") or el.get("text") or "").strip()
            alnum = [c for c in txt if c.isalnum()]
            if len(alnum) >= 3 and any(c.isalpha() for c in alnum) and len(txt) <= 100 and not txt.endswith('.'):
                el["type"] = "heading"
                _set_heading_level(el, 1)
                promoted_title = True
                break

        if promoted_title:
            return "promoted document title on page 1 to H1"

        # Otherwise shift highest heading to H1
        shift = min(lv for _, lv in headings) - 1
        for el, lv in headings:
            _set_heading_level(el, lv - shift)
        # Ensure only the first promoted heading remains H1
        first_h1_found = False
        for el, lv in headings:
            if (lv - shift) == 1:
                if not first_h1_found:
                    first_h1_found = True
                else:
                    _set_heading_level(el, 2)
        return f"no H1: heading levels moved up by {shift} (H{shift + 1} -> H1)"

    for el, inside in ordered:
        if inside or _is_excluded_from_heading(el, inside) or _type(el) != "paragraph":
            continue
        text = (el.get("content") or el.get("text") or "").strip()
        alnum = [c for c in text if c.isalnum()]
        if len(alnum) >= 3 and any(c.isalpha() for c in alnum):
            el["type"] = "heading"
            _set_heading_level(el, 1)
            return f"no heading: first paragraph promoted to H1 ({text.strip()[:40]!r})"
    return ""
