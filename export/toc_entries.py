# GUI_V1/export/toc_entries.py
"""
TOC entries: final shape of every TOCI (Digital, Hybrid and Scanned).

Rule: a TOC / TOCI never contains an Lbl. The entry number ("1", "1.1", "I")
printed before the title is part of the entry text:

  * number inside the entry's link area  ->  TOCI > Reference > Link > ["1.1 Background ...", OBJR]
  * number outside the link area         ->  TOCI > Reference > ["1.1", Link > [..., OBJR]]
  * entry without any link               ->  TOCI > Reference > ["1.1 Background ... 1"]
                                             (a GoTo link to the heading may be added later)

Scanned PDFs: the link nodes delivered by the extractor are not trusted (their
annotation ids are often attached to the wrong line). Every entry is matched
again, by position, to the PDF's own /Link annotation that lies on that line,
in the same top-to-bottom order (so a drifted box cannot take the next line's
link). The entry text (number, title, leader dots, page number) becomes the
Link's text; the number stays plain Reference text before the Link only when
the link rectangle clearly starts after it.
"""
import re

from export.structure_enforcer import (
    _type, TOC_TYPES, TOCI_TYPES, LBL_TYPES, REF_TYPES, LINK_TYPES,
)

_LEADER_TAIL = re.compile(r'(\s*(?:[.…·_]\s*){2,}\s*\S*\s*$)|(\s+(?:\d{1,4}|[ivxlcdm]{1,6})\s*$)', re.I)


def _bbox(el):
    b = el.get("bounding_box")
    if isinstance(b, (list, tuple)) and len(b) == 4:
        try:
            x0, y0, x1, y1 = [float(v) for v in b]
            return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
        except Exception:
            return None
    return None


def _split_tail(text):
    """'Background and Overview . . . . 1' -> ('Background and Overview', '. . . . 1')."""
    t = (text or "").strip()
    m = _LEADER_TAIL.search(t)
    if not m or m.start() == 0:
        return t, ""
    return t[:m.start()].rstrip(), t[m.start():].strip()


def _strip_label(text, label):
    t = (text or "").strip()
    if label and t.startswith(label):
        return t[len(label):].strip()
    return t


class _PageLinks:
    """The input PDF's /Link annotations of each page (PDF user space)."""

    def __init__(self, pdf_path):
        self.pages = {}
        self.frames = {}
        if not pdf_path:
            return
        try:
            import pikepdf
            from renderer.page_geometry import PageFrame
            with pikepdf.Pdf.open(pdf_path) as pdf:
                for pi, pg in enumerate(pdf.pages):
                    self.frames[pi + 1] = PageFrame(pg)
                    out = []
                    for ai, a in enumerate(pg.obj.get("/Annots") or []):
                        try:
                            if str(a.get("/Subtype")) != "/Link":
                                continue
                            r = [float(v) for v in a.Rect]
                            out.append({"rid": f"page_{pi}_annot_{ai}",
                                        "rect": [min(r[0], r[2]), min(r[1], r[3]), max(r[0], r[2]), max(r[1], r[3])],
                                        "used": False})
                        except Exception:
                            continue
                    self.pages[pi + 1] = out
        except Exception as e:
            print(f"[TOC] could not read link annotations: {e}")

    def to_pdf(self, page, bbox):
        fr = self.frames.get(page)
        if fr is None or bbox is None:
            return bbox
        return fr.json_bbox_to_pdf(bbox)

    def to_json(self, page, rect):
        """PDF rect -> JSON box (unrotated pages: a translation)."""
        fr = self.frames.get(page)
        if fr is None:
            return list(rect)
        if fr.rotation:
            return None
        cx, cy = fr.crop_offset
        return [rect[0] - cx, rect[1] - cy, rect[2] - cx, rect[3] - cy]

    def covering(self, page, pdf_box, used_ok=False):
        """Link annotations lying on the line of pdf_box (best overlap first)."""
        res = []
        if pdf_box is None:
            return res
        for a in self.pages.get(page, []):
            if a["used"] and not used_ok:
                continue
            r = a["rect"]
            vo = min(r[3], pdf_box[3]) - max(r[1], pdf_box[1])
            ho = min(r[2], pdf_box[2]) - max(r[0], pdf_box[0])
            hmin = max(min(r[3] - r[1], pdf_box[3] - pdf_box[1]), 1.0)
            if vo >= 0.5 * hmin and ho > 0:
                res.append((ho, a))
        res.sort(key=lambda t: -t[0])
        return [a for _, a in res]

    def assign(self, entries):
        """One-to-one match of TOC entry lines to link annotations.
        entries: [(key, page, pdf_box)] in reading order.

        Extracted boxes can drift by a line or more down a long TOC page, so the
        nearest link is not reliable. Entries and the links that start at an
        entry's left edge are both read top to bottom and aligned in that order
        (an order-preserving alignment, like a diff), which pairs entry n with
        link n even when every box is shifted. Links that start inside a title
        (e.g. a link to a theorem) are never an entry's own link. Entries left
        over are matched by overlap with the remaining links."""
        out, used = {}, set()
        by_page = {}
        for key, page, b in entries:
            if b is not None:
                by_page.setdefault(page, []).append((key, b))
        for page, ents in by_page.items():
            ents.sort(key=lambda t: (-(t[1][1] + t[1][3]) / 2.0, t[1][0]))
            lefts = sorted({round(b[0], 0) for _, b in ents})
            starts = [a for a in self.pages.get(page, [])
                      if any(abs(a["rect"][0] - x) <= 6.0 for x in lefts)]
            starts.sort(key=lambda a: (-(a["rect"][1] + a["rect"][3]) / 2.0, a["rect"][0]))
            n, m = len(ents), len(starts)

            def score(i, j):
                b = ents[i][1]
                r = starts[j]["rect"]
                if abs(r[0] - b[0]) > 6.0:
                    return None
                h = max(b[3] - b[1], 4.0)
                dy = abs((r[1] + r[3]) / 2.0 - (b[1] + b[3]) / 2.0)
                if dy > 3.0 * h:
                    return None
                return 10.0 - dy / (3.0 * h)   # matching more entries always wins

            # dp[i][j]: best total for ents[i:], starts[j:]
            dp = [[0.0] * (m + 1) for _ in range(n + 1)]
            for i in range(n - 1, -1, -1):
                for j in range(m - 1, -1, -1):
                    best = max(dp[i + 1][j], dp[i][j + 1])
                    sc = score(i, j)
                    if sc is not None:
                        best = max(best, sc + dp[i + 1][j + 1])
                    dp[i][j] = best
            i = j = 0
            while i < n and j < m:
                sc = score(i, j)
                if sc is not None and abs(dp[i][j] - (sc + dp[i + 1][j + 1])) < 1e-9:
                    out[ents[i][0]] = starts[j]
                    used.add(id(starts[j]))
                    i += 1
                    j += 1
                elif abs(dp[i][j] - dp[i + 1][j]) < 1e-9:
                    i += 1
                else:
                    j += 1
            # leftovers: best vertical overlap with any remaining link on the line
            for key, b in ents:
                if key in out:
                    continue
                bh = max(b[3] - b[1], 1.0)
                cands = []
                for a in self.pages.get(page, []):
                    if id(a) in used:
                        continue
                    r = a["rect"]
                    vo = min(r[3], b[3]) - max(r[1], b[1])
                    ho = min(r[2], b[2]) - max(r[0], b[0])
                    if vo > 0.5 * max(min(r[3] - r[1], bh), 1.0) and ho > 0 and r[0] <= b[0] + 0.5 * (b[2] - b[0]):
                        cands.append((vo, a))
                if cands:
                    a = max(cands, key=lambda t: t[0])[1]
                    out[key] = a
                    used.add(id(a))
        for a in [x for pg in self.pages.values() for x in pg]:
            if id(a) in used:
                a["used"] = True
        return out

    def point_in_link(self, page, x, y, tol=1.5):
        for a in self.pages.get(page, []):
            r = a["rect"]
            if r[0] - tol <= x <= r[2] + tol and r[1] - tol <= y <= r[3] + tol:
                return True
        return False


def _label_box(entry_box, label):
    """Approximate box of the entry number at the start (left) of the line."""
    x0, y0, x1, y1 = entry_box
    h = max(y1 - y0, 4.0)
    w = min(max(0.42 * h * max(len(label), 1), 0.5 * h), (x1 - x0) / 2.0)
    return [x0, y0, x0 + w, y1]


def finalize_toc_entries(doc_json, input_pdf=None, document_type=""):
    """Applies the TOC rules above to the legacy semantic JSON (in place).
    Returns a dict of counters for the log."""
    dt = (document_type or "").upper()
    scanned = not ("DIGITAL" in dt or "HYBRID" in dt)
    links = _PageLinks(input_pdf)
    stats = {"labels_moved_into_link": 0, "labels_before_link": 0, "labels_into_text": 0,
             "links_matched": 0, "links_dropped": 0}
    counter = [0]

    def new_id(base, suffix):
        counter[0] += 1
        return f"{base}-{suffix}{counter[0]}"

    def fix_entry(toci, page):
        kids = toci.get("kids") or []
        labels = [k for k in kids if _type(k) in LBL_TYPES]
        label = " ".join((k.get("content") or k.get("actual_text") or "").strip() for k in labels).strip()
        rest = [k for k in kids if _type(k) not in LBL_TYPES]
        refs = [k for k in rest if _type(k) in REF_TYPES]
        if labels and not refs:
            ref = {"type": "reference", "id": new_id(toci.get("id", "toci"), "ref"), "content": "",
                   "bounding_box": toci.get("bounding_box"), "page_number": toci.get("page_number"),
                   "reading_order": toci.get("reading_order"), "kids": []}
            rest.insert(0, ref)
            refs = [ref]
        toci["kids"] = rest
        if not refs:
            return
        ref = refs[0]
        # the entry's own line (a TOCI box may also cover its sub-TOC)
        entry_box = _bbox(ref) or _bbox(toci)
        link_kids = [k for k in (ref.get("kids") or []) if _type(k) in LINK_TYPES]
        others = [k for k in (ref.get("kids") or []) if _type(k) not in LINK_TYPES]

        if scanned and entry_box is not None:
            # ---- rebuild the entry's Link from the PDF's own annotation on this line
            full = (toci.get("content") or "").strip()
            if not full:
                parts = [label, (ref.get("content") or "").strip()] + [(k.get("content") or "").strip() for k in link_kids]
                full = " ".join(p for p in parts if p)
            body = _strip_label(full, label)
            title, tail = _split_tail(body)
            pdf_box = links.to_pdf(page, entry_box)
            a0 = assigned.get(id(ref)) or assigned.get(id(toci))
            cands = [a0] if a0 is not None else []
            if link_kids:
                stats["links_dropped"] += len(link_kids)
            if cands:
                a = cands[0]
                r = a["rect"]
                lb = links.to_pdf(page, _label_box(entry_box, label)) if label else None
                label_in = bool(label) and lb is not None and r[0] - 1.5 <= (lb[0] + lb[2]) / 2 <= r[2] + 1.5
                # leader dots + page number belong to the entry's link (same as the
                # digital result: Link "1.1 Background and Overview ...... 1")
                tail_in = bool(tail)
                text = " ".join(p for p in [label if label_in else "", title, tail if tail_in else ""] if p)
                jr = links.to_json(page, r)
                lbox = [max(entry_box[0], jr[0]) if not label_in else entry_box[0], entry_box[1], entry_box[2], entry_box[3]] if jr else list(entry_box)
                if lbox[2] - lbox[0] < 4:
                    lbox = list(entry_box)
                annot = {"runtime_annot_id": a["rid"], "rect": r}
                link = {"type": "link", "id": new_id(ref.get("id", "ref"), "link"), "content": text,
                        "bounding_box": lbox, "page_number": page, "reading_order": ref.get("reading_order"),
                        "annotation": annot,
                        "kids": [{"type": "objr", "id": new_id(ref.get("id", "ref"), "objr"), "page_number": page,
                                  "annotation": dict(annot)}]}
                ref["kids"] = others + [link]
                ref["content"] = ""
                ref.pop("_toc_target", None)
                if label and not label_in:
                    ref["lead_text"] = label
                    ref["lead_bbox"] = [entry_box[0], entry_box[1], max(lbox[0] - 0.5, entry_box[0] + 2.0), entry_box[3]]
                    stats["labels_before_link"] += 1
                elif label:
                    stats["labels_moved_into_link"] += 1
                if tail and not tail_in:
                    ref["tail_text"] = tail
                    ref["tail_bbox"] = [min(lbox[2] + 0.5, entry_box[2] - 2.0), entry_box[1], entry_box[2], entry_box[3]]
                stats["links_matched"] += 1
            else:
                # no link on this line: the whole entry is plain Reference text
                ref["kids"] = others
                ref["content"] = full
                if label:
                    stats["labels_into_text"] += 1
            return

        # ---- Digital / Hybrid: glyph geometry tags the link; only the number moves
        if not label:
            return
        label_in = False
        if entry_box is not None:
            lb = links.to_pdf(page, _label_box(entry_box, label))
            if lb is not None:
                label_in = links.point_in_link(page, (lb[0] + lb[2]) / 2, (lb[1] + lb[3]) / 2)
        if link_kids and label_in:
            lk = link_kids[0]
            lk["content"] = (label + " " + _strip_label(lk.get("content") or "", label)).strip()
            stats["labels_moved_into_link"] += 1
        elif link_kids:
            ref["content"] = (label + " " + (ref.get("content") or "")).strip()
            stats["labels_before_link"] += 1
        else:
            ref["content"] = (label + " " + _strip_label(ref.get("content") or "", label)).strip()
            stats["labels_into_text"] += 1

    def walk(els, page, in_toc=False):
        for e in els or []:
            t = _type(e)
            if t in TOCI_TYPES:
                fix_entry(e, e.get("page_number") or page)
            walk(e.get("kids") or [], page, in_toc or t in TOC_TYPES)

    assigned = {}
    if scanned:
        entries = []

        def gather(els, page):
            for e in els or []:
                if _type(e) in TOCI_TYPES:
                    refs = [k for k in (e.get("kids") or []) if _type(k) in REF_TYPES]
                    pg = e.get("page_number") or page
                    if refs:
                        entries.append((id(refs[0]), pg, links.to_pdf(pg, _bbox(refs[0]) or _bbox(e))))
                    else:
                        entries.append((id(e), pg, links.to_pdf(pg, _bbox(e))))
                gather(e.get("kids") or [], page)
        for p in doc_json.get("pages", []):
            gather(p.get("elements", []), p.get("page_number"))
        assigned = links.assign(entries)

    for p in doc_json.get("pages", []):
        walk(p.get("elements", []), p.get("page_number"))
    return stats
