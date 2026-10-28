import json
import pikepdf
import pymupdf
import decimal
from pikepdf import Name, Dictionary, Array, Operator, ContentStreamInstruction

def multiply_matrices(A, B):
    return [
        A[0]*B[0] + A[1]*B[2],
        A[0]*B[1] + A[1]*B[3],
        A[2]*B[0] + A[3]*B[2],
        A[2]*B[1] + A[3]*B[3],
        A[4]*B[0] + A[5]*B[2] + B[4],
        A[4]*B[1] + A[5]*B[3] + B[5]
    ]

def parse_cmap(cmap_bytes):
    cmap = {}
    try:
        text = cmap_bytes.decode('latin1')
        in_bfchar = False
        for line in text.splitlines():
            line = line.strip()
            if 'beginbfchar' in line:
                in_bfchar = True
                continue
            if 'endbfchar' in line:
                in_bfchar = False
                continue
            if in_bfchar and line.startswith('<'):
                parts = line.split('>')
                if len(parts) >= 2:
                    code_hex = parts[0].strip('<')
                    uni_hex = parts[1].strip().strip('<')
                    code = int(code_hex, 16)
                    chars = ''
                    for i in range(0, len(uni_hex), 4):
                        if i+4 <= len(uni_hex):
                            chars += chr(int(uni_hex[i:i+4], 16))
                    cmap[code] = chars
    except:
        pass
    return cmap

def decode_string(pdf_str, cmap, font_type):
    if not cmap:
        return ""
    result = ""
    is_2byte = ('CID' in font_type or 'Type0' in font_type)
    bytes_val = bytes(pdf_str)
    
    i = 0
    while i < len(bytes_val):
        if is_2byte and i + 1 < len(bytes_val):
            code = (bytes_val[i] << 8) | bytes_val[i+1]
            i += 2
        else:
            code = bytes_val[i]
            i += 1
            
        if code in cmap:
            result += cmap[code]
        else:
            result += '?'
    return result

class DigitalStreamTagger:
    @staticmethod
    def _get_font_resources(page_obj):
        """Extract CMap, font type, and width metrics from a page's font resources."""
        cmaps = {}
        font_types = {}
        fonts_metrics = {}
        if '/Resources' in page_obj and '/Font' in page_obj.Resources:
            for alias, font_obj in page_obj.Resources.Font.items():
                alias_str = str(alias)
                font_types[alias_str] = str(font_obj.get('/Subtype', ''))
                if '/ToUnicode' in font_obj:
                    cmap_bytes = font_obj.ToUnicode.read_bytes()
                    cmaps[alias_str] = parse_cmap(cmap_bytes)

                native_widths = {}
                if '/Widths' in font_obj and '/FirstChar' in font_obj:
                    first_char = int(font_obj.FirstChar)
                    for idx, w in enumerate(font_obj.Widths):
                        native_widths[first_char + idx] = float(w)

                basefont = str(font_obj.get('/BaseFont', 'Helvetica'))
                if basefont.startswith('/'):
                    basefont = basefont[1:]
                try:
                    fallback = pymupdf.Font(basefont)
                except Exception:
                    fallback = pymupdf.Font("helv")

                fonts_metrics[alias_str] = {
                    'native_widths': native_widths,
                    'fallback': fallback
                }
        return cmaps, font_types, fonts_metrics

    @staticmethod
    def _compute_advance(op, operands, Tf_name, Tfs, Tc, Tw, Tz, cmaps, font_types, fonts_metrics):
        """Compute advance_x for a text-drawing operator."""
        native_widths = fonts_metrics.get(Tf_name, {}).get('native_widths', {})
        fallback = fonts_metrics.get(Tf_name, {}).get('fallback')

        def char_width(c):
            code = ord(c)
            if code in native_widths:
                return (native_widths[code] / 1000.0) * Tfs
            if fallback:
                return fallback.text_length(c, fontsize=Tfs)
            return 0.0

        advance_x = 0.0
        if op in ('Tj', "'", '"'):
            text_operand = operands[0] if op == 'Tj' else (operands[2] if op == '"' else operands[0])
            decoded = decode_string(text_operand, cmaps.get(Tf_name, {}), font_types.get(Tf_name, ''))
            for c in decoded:
                advance_x += (char_width(c) + Tc + (Tw if c == ' ' else 0)) * (Tz / 100.0)
        elif op == 'TJ':
            for item in operands[0]:
                if isinstance(item, pikepdf.String):
                    decoded = decode_string(item, cmaps.get(Tf_name, {}), font_types.get(Tf_name, ''))
                    for c in decoded:
                        advance_x += (char_width(c) + Tc + (Tw if c == ' ' else 0)) * (Tz / 100.0)
                elif isinstance(item, (int, float, decimal.Decimal)):
                    advance_x += (-float(item) / 1000.0) * Tfs * (Tz / 100.0)
        return advance_x

    @staticmethod
    def tag_selection(pdf, page_obj, instructions, target_bbox, new_tag_name, next_mcid):
        """
        Wraps native text content that physically intersects `target_bbox` with BDC/EMC.

        - Uses exact physical text bounding box (not just text origin).
        - Detects existing marked-content and rejects if selected content is already tagged.
        - Handles discontiguous selections as separate contiguous ranges, each with its own MCID.
        - Never wraps non-selected content between two selected operators.

        Returns: (new_instructions, list_of_used_mcids, bool_tagged)
        On rejection (already tagged): returns (original_instructions, [], False)
        """
        crop_box = [float(x) for x in page_obj.get('/CropBox', page_obj.get('/MediaBox', [0, 0, 612, 792]))]
        crop_x0 = crop_box[0]
        tx0, ty0, tx1, ty1 = target_bbox

        cmaps, font_types, fonts_metrics = DigitalStreamTagger._get_font_resources(page_obj)

        # ----------------------------------------------------------------
        # PASS 1: Walk the stream to compute exact bbox for every text op.
        # Track mc_depth + current MCID to detect already-tagged content.
        # ----------------------------------------------------------------
        gs_stack = []
        CTM = [1, 0, 0, 1, 0, 0]
        Tm = [1, 0, 0, 1, 0, 0]
        Tlm = [1, 0, 0, 1, 0, 0]
        Tf_name = ""
        Tfs = 1.0
        Tc = 0.0
        Tw = 0.0
        Tz = 100.0
        Ts = 0.0
        Tl = 0.0

        # Marked-content tracking
        mc_depth = 0
        mc_tag_stack = []   # list of (tag_name, mcid_or_None)

        # Result: list of (idx, intersects: bool, already_tagged: bool, mcid_if_tagged)
        text_op_info = []  # (instruction_index, intersects, already_tagged)

        for i, inst in enumerate(instructions):
            op = str(inst.operator)
            operands = inst.operands

            if op == 'q':
                gs_stack.append((CTM.copy(), Tc, Tw, Tz, Ts, Tl, Tf_name, Tfs))
            elif op == 'Q':
                if gs_stack:
                    CTM, Tc, Tw, Tz, Ts, Tl, Tf_name, Tfs = gs_stack.pop()
            elif op == 'cm':
                m = [float(x) for x in operands]
                CTM = multiply_matrices(m, CTM)
            elif op == 'BMC':
                mc_depth += 1
                mc_tag_stack.append((str(operands[0]) if operands else '', None))
            elif op == 'BDC':
                mc_depth += 1
                tag_name = str(operands[0]) if operands else ''
                mcid_val = None
                if len(operands) >= 2:
                    props = operands[1]
                    if isinstance(props, pikepdf.Dictionary):
                        m = props.get('/MCID')
                        if m is not None:
                            mcid_val = int(m)
                mc_tag_stack.append((tag_name, mcid_val))
            elif op == 'EMC':
                mc_depth -= 1
                if mc_tag_stack:
                    mc_tag_stack.pop()
            elif op == 'BT':
                Tm = [1, 0, 0, 1, 0, 0]
                Tlm = [1, 0, 0, 1, 0, 0]
            elif op == 'Tf':
                Tf_name = str(operands[0])
                Tfs = float(operands[1])
            elif op == 'Tc':
                Tc = float(operands[0])
            elif op == 'Tw':
                Tw = float(operands[0])
            elif op == 'Tz':
                Tz = float(operands[0])
            elif op == 'Ts':
                Ts = float(operands[0])
            elif op == 'TL':
                Tl = float(operands[0])
            elif op == 'Tm':
                Tm = [float(x) for x in operands]
                Tlm = Tm.copy()
            elif op in ('Td', 'TD'):
                tx, ty = float(operands[0]), float(operands[1])
                if op == 'TD':
                    Tl = -ty
                Tm = multiply_matrices([1, 0, 0, 1, tx, ty], Tlm)
                Tlm = Tm.copy()
            elif op == 'T*':
                Tm = multiply_matrices([1, 0, 0, 1, 0, -Tl], Tlm)
                Tlm = Tm.copy()

            if op in ('Tj', 'TJ', "'", '"'):
                # Update Tm for line-break ops
                if op == "'":
                    Tm = multiply_matrices([1, 0, 0, 1, 0, -Tl], Tlm)
                    Tlm = Tm.copy()
                elif op == '"':
                    Tw = float(operands[0])
                    Tc = float(operands[1])
                    Tm = multiply_matrices([1, 0, 0, 1, 0, -Tl], Tlm)
                    Tlm = Tm.copy()

                TRM = multiply_matrices(Tm, CTM)
                origin_x = TRM[4] - crop_x0
                origin_y = TRM[5]
                # Scale factor for text height
                scale_y = abs(TRM[3]) if abs(TRM[3]) > 0.01 else Tfs

                advance_x = DigitalStreamTagger._compute_advance(
                    op, operands, Tf_name, Tfs, Tc, Tw, Tz, cmaps, font_types, fonts_metrics
                )

                # Exact fragment bounding box (PDF y-up coords)
                frag_x0 = origin_x
                frag_y0 = origin_y
                frag_x1 = origin_x + advance_x
                frag_y1 = origin_y + scale_y

                # Intersection test
                intersects = not (frag_x1 < tx0 or frag_x0 > tx1 or frag_y1 < ty0 or frag_y0 > ty1)

                # Already-tagged test: mc_depth > 0 means inside existing structure
                already_tagged = mc_depth > 0

                text_op_info.append((i, intersects, already_tagged))

                # Advance Tm
                Tm = multiply_matrices([1, 0, 0, 1, advance_x, 0], Tm)

        # ----------------------------------------------------------------
        # PASS 2: Validate — reject if any selected content is already tagged
        # ----------------------------------------------------------------
        for idx, intersects, already_tagged in text_op_info:
            if intersects and already_tagged:
                # Selected content already belongs to a structural element — reject
                return list(instructions), [], False

        # ----------------------------------------------------------------
        # PASS 3: Build contiguous ranges of selected instructions.
        # A "contiguous range" is a maximal run of consecutive text_op_info
        # entries where all gaps between selected entries contain only
        # non-text-drawing operators (graphical state, etc.) — never other
        # text operators. If a non-selected text operator lies between two
        # selected ones, that breaks the range.
        # ----------------------------------------------------------------
        selected_indices = [idx for idx, intersects, _ in text_op_info if intersects]
        if not selected_indices:
            return list(instructions), [], False

        # Build contiguous blocks by checking if any un-selected TEXT op
        # falls strictly between two consecutively selected text ops.
        # text_op_info is in stream order; get the full list of text op indices.
        all_text_indices = [idx for idx, _, _ in text_op_info]
        all_text_idx_set = set(all_text_indices)

        ranges = []  # list of (first_instr_idx, last_instr_idx) — stream indices to wrap
        i = 0
        while i < len(selected_indices):
            range_start = selected_indices[i]
            range_end = selected_indices[i]
            j = i + 1
            while j < len(selected_indices):
                next_sel = selected_indices[j]
                prev_sel = selected_indices[j - 1]
                # Check for any non-selected TEXT op between prev_sel and next_sel
                between_text_ops = [
                    tidx for tidx in all_text_indices
                    if prev_sel < tidx < next_sel
                ]
                # Check if all between text ops are also selected
                between_unselected = [
                    tidx for tidx in between_text_ops
                    if tidx not in set(selected_indices)
                ]
                if between_unselected:
                    # Non-selected text between them: break here
                    break
                range_end = next_sel
                j += 1
            ranges.append((range_start, range_end))
            i = j

        if not ranges:
            return list(instructions), [], False

        # ----------------------------------------------------------------
        # PASS 4: Insert BDC/EMC pairs for each contiguous range.
        # Process in reverse order so insertions don't shift earlier indices.
        # Each range gets its own MCID.
        # ----------------------------------------------------------------
        instructions_copy = list(instructions)
        used_mcids = []
        mcid_counter = next_mcid

        for (rstart, rend) in reversed(ranges):
            mcid = mcid_counter
            mcid_counter += 1
            used_mcids.insert(0, mcid)  # maintain ascending order

            # Insert EMC after last instruction in range
            instructions_copy.insert(
                rend + 1,
                ContentStreamInstruction([], Operator("EMC"))
            )
            # Insert BDC before first instruction in range
            instructions_copy.insert(
                rstart,
                ContentStreamInstruction(
                    [Name(f"/{new_tag_name}"), Dictionary(MCID=mcid)],
                    Operator("BDC")
                )
            )

        return instructions_copy, used_mcids, True


    # ======================================================================
    # JSON-BBOX + CONTENT MATCHING TAGGER  (Digital / Hybrid)
    # ======================================================================
    #
    # How it works
    # ------------
    # 1. ContentInterpreter walks the page stream (and every Form XObject it
    #    draws) and measures every glyph exactly: position, size and unicode.
    #    Widths come from the font's own /Widths (+ Type3 /FontMatrix, Type0 /W),
    #    looked up by CHARACTER CODE, so the boxes match what a viewer draws.
    # 2. Every semantic JSON node with a bbox becomes a candidate. Each glyph is
    #    given to the node whose bbox contains it; when bboxes overlap the tie is
    #    broken by CONTENT (does that node's text contain this character?), then
    #    by FontMapper's precise run boxes, then by the deepest / smallest node.
    # 3. A text operator whose glyphs belong to several nodes is split into
    #    several TJ operators at glyph boundaries. Because the text matrix
    #    advances the same way, the page renders identically.
    # 4. Everything that draws but is not content (paths, backgrounds, shadings,
    #    decorative images, text outside every node) is wrapped as /Artifact so
    #    nothing on the page is left untagged. Images / vector drawings inside a
    #    figure bbox go to that Figure.
    # 5. No glyph is ever added, removed or moved. Only BDC/EMC are inserted.
    #
    # Returns (new_instructions, {node_id: [mcids]}, next_mcid)
    # A per-page report is stored in DigitalStreamTagger.last_report.

    last_report = None

    TEXT_TYPES_EXCLUDED = {"figure", "objr", "OBJR", "object_reference", "form"}
    # PDF/UA: these tags hold only other tags, never content. A text summary the
    # extractor attached to them (e.g. a table's whole text) must not pull glyphs
    # out of the cells into the Table itself ("content in an inadmissible location").
    STRUCTURE_ONLY_TAGS = {"Table", "TR", "THead", "TBody", "TFoot", "L", "TOC"}

    @staticmethod
    def _norm_chars(text):
        import unicodedata
        t = unicodedata.normalize("NFKD", text or "")
        return [c.lower() for c in t if c.isalnum()]

    @staticmethod
    def _collect_nodes(elements, node_id_to_tag, crop_offset, font_runs_by_id):
        """Flattens the JSON element tree into candidate nodes (user-space bboxes)."""
        from collections import Counter
        cx, cy = crop_offset
        text_nodes, figure_nodes, container_nodes = [], [], []

        def bbox_of(el):
            g = el.get("_geom")
            if isinstance(g, dict) and "left" in g:
                try:
                    return (float(g["left"]), float(g["bottom"]), float(g["right"]), float(g["top"]))
                except Exception:
                    pass
            b = el.get("bounding_box")
            if isinstance(b, (list, tuple)) and len(b) == 4:
                try:
                    x0, y0, x1, y1 = [float(v) for v in b]
                    return (min(x0, x1) + cx, min(y0, y1) + cy, max(x0, x1) + cx, max(y0, y1) + cy)
                except Exception:
                    return None
            return None

        def walk(els, depth, ancestors=()):
            for el in els or []:
                el_id = str(el.get("id"))
                el_type = el.get("type") or (el.get("semantic") or {}).get("type") or ""
                tag = node_id_to_tag.get(el_id, "P") if node_id_to_tag else "P"
                bbox = bbox_of(el)
                content = el.get("content") or el.get("text") or ""
                if bbox and bbox[2] > bbox[0] and bbox[3] > bbox[1]:
                    area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
                    if el_type == "figure" or tag == "Figure":
                        figure_nodes.append({"id": el_id, "bbox": bbox, "area": area, "tag": tag, "depth": depth})
                    
                    if el_type == "formula" or tag == "Formula":
                        # Formula has text but also vector paths like fraction bars
                        figure_nodes.append({"id": el_id, "bbox": bbox, "area": area, "tag": tag, "depth": depth})

                    has_text_kids = bool(el.get("kids")) and any(
                        k.get("tag") in ("Formula", "Span", "Reference", "Link") or
                        k.get("type") in ("formula", "span", "reference", "link", "hyperlink")
                        for k in el["kids"]
                    )
                    if el_type != "figure" and tag != "Figure" and content.strip() and el_type not in DigitalStreamTagger.TEXT_TYPES_EXCLUDED and tag != "OBJR" \
                            and tag not in DigitalStreamTagger.STRUCTURE_ONLY_TAGS \
                            and not (tag in ("LI", "TOCI") and el.get("kids")) \
                            and not has_text_kids:
                        runs = []
                        for r in (font_runs_by_id or {}).get(el_id, []) or []:
                            rb = r.get("bbox")
                            if rb and len(rb) == 4:
                                runs.append((rb[0] + cx, rb[1] + cy, rb[2] + cx, rb[3] + cy))
                        chars = DigitalStreamTagger._norm_chars(content)
                        is_label = el_type == "list_label" or tag == "Lbl"
                        text_nodes.append({
                            "id": el_id, "bbox": bbox, "area": area, "tag": tag, "depth": depth,
                            "type": el_type, "content": content, "runs": runs,
                            "remaining": Counter(chars), "total": max(len(chars), 1),
                            "seq": chars, "ptr": 0,
                            "ancestors": set(ancestors), "parent": ancestors[-1] if ancestors else None,
                            "is_label": is_label,
                            "label_seq": [c for c in content if not c.isspace()] if is_label else [],
                            "label_pos": 0,
                            "inline_links": [l for l in ((el.get("analysis") or {}).get("inline_links") or [])
                                             if isinstance(l, dict) and l.get("target_url") and not l.get("runtime_annot_id")],
                        })
                    elif el_type not in DigitalStreamTagger.TEXT_TYPES_EXCLUDED and tag not in ("OBJR", "Artifact"):
                        # Empty container with a bbox (table cell, list item, ...). Used only as a
                        # fallback owner for native text the JSON content forgot to include.
                        container_nodes.append({"id": el_id, "bbox": bbox, "area": area, "tag": tag,
                                                "depth": depth, "type": el_type})
                kids = el.get("kids")
                if kids:
                    walk(kids, depth + 1, ancestors + (el_id,))

        walk(elements, 0)
        return text_nodes, figure_nodes, container_nodes

    @staticmethod
    def _inside(px, py, b, tol=0.0):
        return b[0] - tol <= px <= b[2] + tol and b[1] - tol <= py <= b[3] + tol

    @staticmethod
    def _rect_dist(px, py, b):
        dx = max(b[0] - px, 0.0, px - b[2])
        dy = max(b[1] - py, 0.0, py - b[3])
        return (dx * dx + dy * dy) ** 0.5

    @staticmethod
    def _overlap_ratio(a, b):
        """intersection(a, b) / area(a)"""
        if not a or not b:
            return 0.0
        ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
        iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
        aa = max((a[2] - a[0]) * (a[3] - a[1]), 1e-6)
        return (ix * iy) / aa

    @staticmethod
    def _assign_glyph(g, text_nodes):
        """Returns the best node for glyph g, or None."""
        import unicodedata
        T = DigitalStreamTagger
        h = max(min(g.height, 40.0), 2.0)
        cands = [n for n in text_nodes if T._inside(g.cx, g.cy, n["bbox"], 1.0)]
        if not cands:
            tol = min(0.6 * h, 8.0)
            cands = [n for n in text_nodes if T._inside(g.cx, g.cy, n["bbox"], tol)]
        if not cands:
            limit = max(1.0 * h, 4.0)
            best, bd = None, None
            for n in text_nodes:
                d = T._rect_dist(g.cx, g.cy, n["bbox"])
                if d <= limit and (bd is None or d < bd):
                    best, bd = n, d
            if best is None:
                return None
            cands = [best]
        # LIST LABELS: a bullet / number glyph that is exactly the next expected
        # character of a candidate Lbl belongs to that Lbl (bullets match bullets).
        for n in cands:
            if n.get("is_label") and n["label_pos"] < len(n["label_seq"]):
                # a marker sits at the start (left edge) of its item, never mid-sentence
                if g.cx > n["bbox"][0] + 1.0 + 2.5 * h * max(1, len(n["label_seq"])):
                    continue
                if T._label_char_matches(n["label_seq"][n["label_pos"]], g.uni):
                    n["label_pos"] += 1
                    T._consume(n, g.uni)
                    return n

        chars = [c.lower() for c in unicodedata.normalize("NFKD", g.uni or "") if c.isalnum()]

        def char_ok(n):
            if not chars:
                return True  # punctuation / symbols are neutral
            return all(n["remaining"].get(c, 0) > 0 for c in chars)

        def is_marker(n):
            # bullets / numbers: a word glyph that the marker's text does not contain is not its own
            return n["type"] == "list_label" or n["tag"] == "Lbl" or len(n["content"].strip()) <= 2

        # A list/TOC label only takes the glyphs of its own marker (matched above);
        # leader dots, spaces and body words go to the sibling Reference / LBody.
        non_labels = [n for n in cands if not n.get("is_label")]
        if non_labels:
            cands = non_labels

        # CONTENT comparison first: keep only nodes whose text still contains this character
        ok = [n for n in cands if char_ok(n)]
        if not ok:
            ok = [n for n in cands if not (chars and is_marker(n))]
            if not ok:
                return None
        if len(ok) == 1:
            T._consume(ok[0], g.uni)
            return ok[0]
        # Parent and child often carry the same text (TOCI > Reference, LI > LBody, P > Link):
        # the most specific node owns the glyph.
        ids = {n["id"] for n in ok}
        ok = [n for n in ok if not any((n["id"] in m["ancestors"]) for m in ok if m is not n)] or ok

        # SEQUENCE comparison: prefer the node whose text CONTINUES with this
        # character at its current reading position (handles overlapping bboxes,
        # e.g. a paragraph box that runs into the footnote below it).
        def seq_ok(n):
            if not chars:
                return 0
            seq, ptr = n["seq"], n["ptr"]
            window = seq[ptr:ptr + 4]
            return 1 if chars[0] in window else 0

        def score(n):
            in_run = 1 if any(T._inside(g.cx, g.cy, rb, 0.75) for rb in n["runs"]) else 0
            strictly_inside = 1 if T._inside(g.cx, g.cy, n["bbox"], 0.0) else 0
            return (seq_ok(n), strictly_inside, in_run, n["depth"], -n["area"])

        best = max(ok, key=score)
        T._consume(best, g.uni)
        return best

    BULLETS = set("\u2022\u25CF\u25CB\u25E6\u25A0\u25A1\u25AA\u25AB\u25BA\u25B8\u2023\u2043\u2013\u2014-*\u00B7"
                  "\u2713\u2714\u27A2\u27A4\u2192\u25C6\u25C7\u2756\uF0B7\uF0A7\uF076\uF0D8\uF0FC\u2219\u25C9\u25CE")

    @staticmethod
    def _label_char_matches(expected, uni):
        u = (uni or "").strip()
        if not u:
            return False
        if u == expected or u.lower() == expected.lower():
            return True
        B = DigitalStreamTagger.BULLETS
        # Symbol fonts (Wingdings/Symbol/ZapfDingbats) often decode to private-use
        # or letter codes; any non-alphanumeric glyph matches a bullet label.
        if expected in B and (u in B or not u.isalnum() or 0xE000 <= ord(u[0]) <= 0xF8FF):
            return True
        return False

    @staticmethod
    def _consume(node, uni):
        import unicodedata
        for c in unicodedata.normalize("NFKD", uni or ""):
            if c.isalnum():
                c = c.lower()
                if node["remaining"].get(c, 0) > 0:
                    node["remaining"][c] -= 1
                seq = node.get("seq")
                if seq is not None:
                    ptr = node.get("ptr", 0)
                    # resync: find the char a little ahead (skips words the JSON
                    # has but the stream draws elsewhere, e.g. hyphenation)
                    for j in range(ptr, min(len(seq), ptr + 40)):
                        if seq[j] == c:
                            node["ptr"] = j + 1
                            break

    @staticmethod
    def _figure_inside_image(box, figure_nodes):
        """An image whose drawn area is larger than what the extractor boxed as
        the figure (white / transparent margins around a photo): the figure box
        lies inside the image. Returns the largest such figure, or None.
        A page background behind a small logo is not matched (the logo covers
        far less than a fifth of it)."""
        T = DigitalStreamTagger
        if not box:
            return None
        best = None
        for f in figure_nodes:
            if T._overlap_ratio(f["bbox"], box) >= 0.8 and T._overlap_ratio(box, f["bbox"]) >= 0.2:
                if best is None or f["area"] > best["area"]:
                    best = f
        return best

    @staticmethod
    def _figure_for_box(box, figure_nodes, min_ratio=0.6):
        T = DigitalStreamTagger
        # A straight line (chart axis, tick, rule) has a zero-width or zero-height
        # box, so its overlap ratio would always be 0. Give it a 1pt thickness.
        if box:
            x0, y0, x1, y1 = box
            if x1 - x0 < 1.0:
                cx = (x0 + x1) / 2
                x0, x1 = cx - 0.5, cx + 0.5
            if y1 - y0 < 1.0:
                cy = (y0 + y1) / 2
                y0, y1 = cy - 0.5, cy + 0.5
            box = (x0, y0, x1, y1)
        best, br = None, 0.0
        for f in figure_nodes:
            r = T._overlap_ratio(box, f["bbox"])
            # equal overlap (a logo inside a big photo's box): the smaller,
            # more specific figure wins
            if r > br + 1e-6 or (best is not None and abs(r - br) <= 1e-6 and r > 0 and f["area"] < best["area"]):
                best, br = f, r
        if best is not None and br >= min_ratio:
            return best
        return None

    # ------------------------------------------------------------- links
    @staticmethod
    def _page_link_annots(page_obj):
        out = []
        for a in (page_obj.get("/Annots") or []):
            try:
                if str(a.get("/Subtype", "")) != "/Link":
                    continue
                r = [float(v) for v in a.get("/Rect", [])]
                if len(r) != 4:
                    continue
                rects = []
                qp = a.get("/QuadPoints")
                if qp is not None and len(qp) >= 8:
                    q = [float(v) for v in qp]
                    for i in range(0, len(q) - 7, 8):
                        xs, ys = q[i:i + 8:2], q[i + 1:i + 8:2]
                        rects.append((min(xs), min(ys), max(xs), max(ys)))
                if not rects:
                    rects = [(min(r[0], r[2]), min(r[1], r[3]), max(r[0], r[2]), max(r[1], r[3]))]
                act = a.get("/A")
                kind, target = "internal", ""
                if act is not None and str(act.get("/S", "")) == "/URI":
                    kind, target = "external", str(act.get("/URI", ""))
                elif act is not None and str(act.get("/S", "")) in ("/Launch", "/GoToR"):
                    kind, target = "external", str(act.get("/F", ""))
                elif act is not None and "/D" in act:
                    target = repr(act.get("/D"))
                elif "/Dest" in a:
                    target = repr(a.get("/Dest"))
                area = sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in rects)
                out.append({"obj": a, "rects": rects, "kind": kind, "target": target, "area": max(area, 0.01)})
            except Exception:
                continue
        return out

    @staticmethod
    def _create_inline_link_annots(pdf, page_obj, text_nodes, glyph_owner, glyph_obj, link_annots):
        """One URI annotation per plain-text URL / e-mail (InlineLinkRule) that has none."""
        T = DigitalStreamTagger
        created = 0
        for n in text_nodes:
            links = n.get("inline_links") or []
            if not links:
                continue
            keys = [k for k in sorted(glyph_obj) if glyph_owner.get(k) == ("node", n["id"])]
            if not keys:
                continue
            chars, idx = [], []
            for k in keys:
                for c in (glyph_obj[k].uni or ""):
                    if not c.isspace():
                        chars.append(c)
                        idx.append(k)
            hay = "".join(chars)
            used_upto = 0
            for l in links:
                needle = "".join(c for c in (l.get("text") or "") if not c.isspace())
                if not needle:
                    continue
                pos = hay.find(needle, used_upto)
                if pos < 0:
                    continue
                used_upto = pos + len(needle)
                gkeys = idx[pos:pos + len(needle)]
                # skip if a real annotation already covers it
                g0 = glyph_obj[gkeys[0]]
                if any(T._inside(g0.cx, g0.cy, r, 0.6) for la in link_annots for r in la["rects"]):
                    continue
                # one rectangle per visual line
                lines = []
                for k in gkeys:
                    g = glyph_obj[k]
                    for ln in lines:
                        if abs(ln["cy"] - g.cy) <= 0.5 * max(g.height, 1.0):
                            b = ln["box"]
                            ln["box"] = (min(b[0], g.bbox[0]), min(b[1], g.bbox[1]), max(b[2], g.bbox[2]), max(b[3], g.bbox[3]))
                            break
                    else:
                        lines.append({"cy": g.cy, "box": g.bbox})
                url = l["target_url"]
                text = l.get("text") or url
                for ln in lines:
                    b = ln["box"]
                    annot = pdf.make_indirect(Dictionary(
                        Type=Name("/Annot"), Subtype=Name("/Link"),
                        Rect=Array([b[0], b[1], b[2], b[3]]), Border=Array([0, 0, 0]), F=4,
                        A=Dictionary(S=Name("/URI"), URI=pikepdf.String(url)),
                        Contents=pikepdf.String(text), P=page_obj.obj,
                    ))
                    if "/Annots" not in page_obj:
                        page_obj.Annots = pdf.make_indirect(Array())
                    page_obj.Annots.append(annot)
                    link_annots.append({"obj": annot, "rects": [b], "kind": "external", "target": url,
                                        "area": max((b[2] - b[0]) * (b[3] - b[1]), 0.01)})
                    created += 1
        return created

    @staticmethod
    def _looks_like_note_ref(text):
        import re
        t = (text or "").strip()
        return bool(re.fullmatch(r"[\(\[]?(?:\d{1,4}|[*\u2020\u2021\u00A7\u00B6]{1,3}|[a-z]|[ivxlcdm]{1,5})[\)\]]?[.,;:]?", t, re.I))

    @staticmethod
    def clean_alt(text):
        """'Introduction ........ 1' / 'Intro . . . . 1' -> 'Introduction 1'."""
        import re
        t = (text or "").replace("\u00A0", " ")
        t = re.sub(r"(?:\s*[.\u2026\u00B7\u2022_]\s*){3,}", " ", t)
        t = re.sub(r"(?:\s+[.\u2026\u00B7_]){2,}\s*", " ", t)   # short spaced leaders ". ."
        t = re.sub(r"\s+[.\u2026\u00B7_](?:\s*[.\u2026\u00B7_])*\s*$", "", t)   # trailing leader stub
        t = re.sub(r"\s+", " ", t).strip()
        return t

    @staticmethod
    def link_alt(text, la):
        import re
        compact = re.sub(r"\s+", "", text or "")
        if la.get("kind") == "external" and re.match(r"^(?:https?://|www\.|mailto:|[\w.+-]+@[\w-]+\.)", compact, re.I):
            return compact            # a URL / e-mail wrapped over two lines
        t = DigitalStreamTagger.clean_alt(text)
        if t:
            return t
        tgt = la.get("target") or ""
        if la.get("kind") == "external" and tgt:
            return tgt[7:] if tgt.lower().startswith("mailto:") else tgt
        return "Link"

    @staticmethod
    def _sequence_anchor(u, text_nodes, artifact_nodes):
        """
        Returns the node whose content continues with this text operator's whole
        text (normalised, >= 4 alphanumerics) at that node's reading pointer,
        searching nodes that contain the run first, then nodes nearby (JSON boxes
        from vision models are often shifted by a line or two). Advances the
        node's pointer on success. None when ambiguous / not found.
        """
        import unicodedata
        T = DigitalStreamTagger
        if not u.glyphs or not u.bbox:
            return None
        w = "".join(c.lower() for g in u.glyphs for c in unicodedata.normalize("NFKD", g.uni or "") if c.isalnum())
        if len(w) < 4:
            return None
        cx, cy = (u.bbox[0] + u.bbox[2]) / 2, (u.bbox[1] + u.bbox[3]) / 2
        h = max(min(max(g.height for g in u.glyphs), 40.0), 4.0)

        def continues(n):
            if n.get("is_label") or n["id"] in artifact_nodes or n["tag"] == "Artifact":
                return -1
            seq = n["seq"]
            ptr = n["ptr"]
            hay = "".join(seq[ptr:ptr + len(w) + 12])
            k = hay.find(w)
            return ptr + k if k >= 0 else -1

        def pick(cands):
            hits = [(n, continues(n)) for n in cands]
            hits = [(n, k) for n, k in hits if k >= 0]
            if not hits:
                return None
            # most specific node (deepest, smallest) wins among equals
            hits.sort(key=lambda t: (-t[0]["depth"], t[0]["area"]))
            n, k = hits[0]
            n["ptr"] = k + len(w)
            return n

        inside = [n for n in text_nodes if T._inside(cx, cy, n["bbox"], 1.0)]
        n = pick(inside)
        if n is not None:
            return n
        # Short runs: only look a few lines around. Long runs (>= 12 letters) are
        # distinctive enough to match anywhere on the page.
        radius = max(3.0 * h, 24.0) if len(w) < 12 else float("inf")
        near = [n for n in text_nodes if n not in inside and T._rect_dist(cx, cy, n["bbox"]) <= radius]
        near.sort(key=lambda n: T._rect_dist(cx, cy, n["bbox"]))
        return pick(near)

    @staticmethod
    def _figure_for_glyph(g, figure_nodes):
        """A figure node that is clearly this glyph (icon): its centre lies in the glyph
        box, or the two boxes overlap by half of the smaller one."""
        T = DigitalStreamTagger
        best, br = None, 0.0
        for f in figure_nodes:
            fb = f["bbox"]
            ix = max(0.0, min(g.bbox[2], fb[2]) - max(g.bbox[0], fb[0]))
            iy = max(0.0, min(g.bbox[3], fb[3]) - max(g.bbox[1], fb[1]))
            inter = ix * iy
            if inter <= 0:
                continue
            ga = max((g.bbox[2] - g.bbox[0]) * (g.bbox[3] - g.bbox[1]), 1e-6)
            fa = max(f["area"], 1e-6)
            if fa > 25 * ga:
                continue  # a big figure region, not an icon: text inside it stays text
            r = inter / min(ga, fa)
            fcx, fcy = (fb[0] + fb[2]) / 2, (fb[1] + fb[3]) / 2
            if T._inside(fcx, fcy, g.bbox, 1.0):
                r = max(r, 0.75)
            if r > br:
                best, br = f, r
        return best if br >= 0.5 else None

    @staticmethod
    def _split_text_instruction(inst, glyphs, owners):
        """
        Splits one text-showing instruction into consecutive TJ pieces, one per
        owner run. Returns list of (owner, [ContentStreamInstruction]).
        Rendering is unchanged: the text matrix advances glyph by glyph exactly
        as it did in the single original operator.
        """
        op = str(inst.operator)
        ops = inst.operands
        prefix = []
        if op == "'":
            items = [ops[0]]
            prefix = [ContentStreamInstruction([], Operator("T*"))]
        elif op == '"':
            items = [ops[2]]
            prefix = [
                ContentStreamInstruction([ops[0]], Operator("Tw")),
                ContentStreamInstruction([ops[1]], Operator("Tc")),
                ContentStreamInstruction([], Operator("T*")),
            ]
        elif op == "Tj":
            items = [ops[0]]
        else:
            items = list(ops[0])

        # owner for (string_index, byte_start)
        owner_at = {}
        for g, o in zip(glyphs, owners):
            owner_at[(g.string_index, g.byte_start)] = (o, g.byte_end)

        pieces = []  # list of [owner, items]

        def push(owner, item):
            if pieces and pieces[-1][0] == owner:
                pieces[-1][1].append(item)
            else:
                pieces.append([owner, [item]])

        s_index = -1
        pending_numbers = []
        for item in items:
            if isinstance(item, pikepdf.String):
                s_index += 1
                raw = bytes(item)
                pos = 0
                while pos < len(raw):
                    o, end = owner_at.get((s_index, pos), (None, None))
                    if end is None:
                        # Should not happen; keep the remaining bytes with the previous owner
                        o = pieces[-1][0] if pieces else (owners[0] if owners else None)
                        end = len(raw)
                    # extend while same owner
                    seg_end = end
                    while seg_end < len(raw):
                        o2, e2 = owner_at.get((s_index, seg_end), (None, None))
                        if e2 is None or o2 != o:
                            break
                        seg_end = e2
                    if pending_numbers:
                        for n in pending_numbers:
                            push(o, n)
                        pending_numbers = []
                    push(o, pikepdf.String(raw[pos:seg_end]))
                    pos = seg_end
            else:
                if pieces:
                    pieces[-1][1].append(item)
                else:
                    pending_numbers.append(item)
        if pending_numbers:
            if pieces:
                pieces[-1][1].extend(pending_numbers)

        out = []
        for i, (owner, its) in enumerate(pieces):
            insts = []
            if i == 0 and prefix:
                insts.extend(prefix)
            insts.append(ContentStreamInstruction([Array(its)], Operator("TJ")))
            out.append((owner, insts))
        if not out and prefix:
            out.append((owners[0] if owners else None, prefix))
        return out

    @staticmethod
    def sanitize_page_objects(pdf, page_obj):
        """
        Removes stale structure left by the input PDF's previous tagging:
        marked content + /StructParents inside Form XObjects, /StructParent on
        images and annotations. Their old keys would collide with the new
        ParentTree (one of the 'duplicate tag' sources). Optional-content
        (/OC) marked content is preserved because it controls visibility.
        """
        seen = set()

        def clean_form(xo):
            try:
                key = xo.objgen
            except Exception:
                key = None
            if key in seen:
                return
            seen.add(key)
            for k in ("/StructParents", "/StructParent"):
                if k in xo:
                    del xo[k]
            try:
                insts = list(pikepdf.parse_content_stream(xo))
            except Exception:
                insts = None
            if insts is not None:
                kept, stack, changed = [], [], False
                for inst in insts:
                    if isinstance(inst, pikepdf.ContentStreamInlineImage):
                        kept.append(inst)
                        continue
                    op = str(inst.operator)
                    if op in ("BDC", "BMC"):
                        keep = bool(inst.operands) and str(inst.operands[0]) == "/OC"
                        stack.append(keep)
                        if keep:
                            kept.append(inst)
                        else:
                            changed = True
                    elif op == "EMC":
                        keep = stack.pop() if stack else False
                        if keep:
                            kept.append(inst)
                        else:
                            changed = True
                    else:
                        kept.append(inst)
                if changed:
                    xo.write(pikepdf.unparse_content_stream(kept))
            walk_resources(xo.get("/Resources"))

        def walk_resources(res):
            if res is None:
                return
            xobjs = res.get("/XObject")
            if xobjs is None:
                return
            for _, xo in xobjs.items():
                try:
                    sub = str(xo.get("/Subtype", ""))
                    if sub == "/Form":
                        clean_form(xo)
                    elif sub == "/Image" and "/StructParent" in xo:
                        del xo["/StructParent"]
                except Exception:
                    continue

        walk_resources(page_obj.get("/Resources"))
        try:
            for annot in page_obj.get("/Annots", []) or []:
                if "/StructParent" in annot:
                    del annot["/StructParent"]
        except Exception:
            pass

    @staticmethod
    def tag_stream(pdf, page_obj, instructions, font_runs_by_id, mcid_start, unsupported_nodes,
                   artifact_nodes=None, node_id_to_tag=None, elements=None, crop_offset=(0.0, 0.0)):
        from collections import Counter, defaultdict
        from plugins.digital.content_geometry import ContentInterpreter, FontInfo, flatten_glyphs, flatten_images

        T = DigitalStreamTagger
        artifact_nodes = artifact_nodes or set()
        node_id_to_tag = node_id_to_tag or {}
        FontInfo._cache.clear()  # object numbers are only unique within one PDF

        # ------------------------------------------------------------------
        # 0. Drop all old marked content delimiters to prevent invalid nesting
        #    (PDF/UA-1 Clause 7.1 requires clean StructTree or /Artifact marking)
        # ------------------------------------------------------------------
        cleaned = []
        for inst in instructions:
            if isinstance(inst, pikepdf.ContentStreamInlineImage):
                cleaned.append(inst)
                continue
            op = str(inst.operator)
            if op in ("BDC", "BMC", "EMC"):
                continue
            cleaned.append(inst)
        instructions = cleaned
        T.sanitize_page_objects(pdf, page_obj)

        # ------------------------------------------------------------------
        # 1. Candidates from the semantic JSON
        # ------------------------------------------------------------------
        if elements is None:
            # Legacy call: build pseudo elements from FontMapper runs
            elements = []
            for nid, runs in (font_runs_by_id or {}).items():
                bbs = [r.get("bbox") for r in runs if r.get("bbox")]
                if bbs:
                    elements.append({
                        "id": nid, "type": "paragraph",
                        "content": "".join(r.get("text", "") for r in runs),
                        "bounding_box": [min(b[0] for b in bbs), min(b[1] for b in bbs),
                                         max(b[2] for b in bbs), max(b[3] for b in bbs)],
                    })
        text_nodes, figure_nodes, container_nodes = T._collect_nodes(elements, node_id_to_tag, crop_offset, font_runs_by_id)

        # ------------------------------------------------------------------
        # 2. Measure the page
        # ------------------------------------------------------------------
        mb = page_obj.get("/MediaBox", [0, 0, 612, 792])
        page_box = tuple(float(v) for v in mb)
        units = ContentInterpreter(page_obj.get("/Resources"), page_box=page_box).run(instructions)

        # ------------------------------------------------------------------
        # 3. Glyph -> node assignment (stream order keeps content consumption sane)
        # ------------------------------------------------------------------
        ART = ("artifact",)
        glyph_owner = {}          # (unit_idx, glyph_idx) -> owner token
        node_vis = defaultdict(list)
        node_invis = defaultdict(list)
        orphans = []
        recovered = []
        anchored_units = 0
        import unicodedata
        for ui, u in enumerate(units):
            if u.kind == "text":
                gl = [(g, u.invisible) for g in u.glyphs]
            elif u.kind == "form":
                gl = flatten_glyphs(u)
            else:
                continue

            # WORD/LINE SEQUENCE ANCHOR: when the whole text of this operator is
            # the continuation of one node's content (at that node's reading
            # position), it belongs to that node, even if the JSON bbox is shifted.
            anchor = T._sequence_anchor(u, text_nodes, artifact_nodes) if u.kind == "text" else None
            # A leading marker ("• ", "– ", "1. ") stays with the normal per-glyph
            # logic so its Lbl can claim it; the anchor covers the words after it.
            first_word = next((i for i, (g, _) in enumerate(gl) if any(c.isalnum() for c in (g.uni or ""))), 0)
            if anchor is not None:
                anchored_units += 1

            for gi, (g, invisible) in enumerate(gl):
                if anchor is not None and gi >= first_word:
                    if g.uni and not g.uni.strip():
                        glyph_owner[(ui, gi)] = None
                        continue
                    glyph_owner[(ui, gi)] = ("node", anchor["id"])
                    (node_invis if invisible else node_vis)[anchor["id"]].append((ui, gi))
                    for c in unicodedata.normalize("NFKD", g.uni or ""):
                        if c.isalnum() and anchor["remaining"].get(c.lower(), 0) > 0:
                            anchor["remaining"][c.lower()] -= 1
                    continue
                if g.uni and not g.uni.strip():
                    glyph_owner[(ui, gi)] = None  # spaces follow their neighbours
                    continue
                # (glyphs whose unicode is unknown are still placed by geometry below)
                # Icons drawn as glyphs (emoji / dingbats) that the JSON marks as a figure.
                fig = T._figure_for_glyph(g, figure_nodes)
                if fig is not None and not any(
                        g.uni in (m["content"] or "") and T._inside(g.cx, g.cy, m["bbox"], 0.0) for m in text_nodes):
                    if fig["tag"] == "Artifact":
                        glyph_owner[(ui, gi)] = ART
                    else:
                        glyph_owner[(ui, gi)] = ("node", fig["id"])
                    continue
                n = T._assign_glyph(g, text_nodes)
                if n is None:
                    # Text the JSON content does not mention. Keep it accessible under the
                    # smallest JSON container (cell, list item...) or figure that holds it.
                    holders = [c for c in container_nodes if T._inside(g.cx, g.cy, c["bbox"], 1.0)]
                    f = T._figure_for_box(g.bbox, figure_nodes, 0.5)
                    if f is None and figure_nodes:
                        cands = [fig for fig in figure_nodes if T._inside(g.cx, g.cy, fig["bbox"], 14.0)]
                        if cands:
                            f = min(cands, key=lambda fig: fig["area"])
                    if holders and not any(ch.isalnum() for ch in (g.uni or "")):
                        glyph_owner[(ui, gi)] = None  # punctuation follows its neighbouring words
                        continue
                    if holders:
                        c = min(holders, key=lambda c: c["area"])
                        # Prefer the nearest text node living inside that container
                        # (e.g. the cell's LBody), so reading order inside it stays natural.
                        inner = [m for m in text_nodes if c["id"] in m["ancestors"]
                                 and m["id"] not in artifact_nodes and m["tag"] != "Artifact"]
                        # Words never belong to a list label (bullet / number marker).
                        if any(ch.isalnum() for ch in (g.uni or "")):
                            inner = [m for m in inner if m["type"] != "list_label" and m["tag"] != "Lbl"] or inner
                        target = min(inner, key=lambda m: T._rect_dist(g.cx, g.cy, m["bbox"]))["id"] if inner else c["id"]
                        glyph_owner[(ui, gi)] = ("node", target)
                        (node_invis if invisible else node_vis)[target].append((ui, gi))
                        recovered.append(g.uni)
                    elif f is not None and f["tag"] != "Artifact":
                        glyph_owner[(ui, gi)] = ("node", f["id"])
                    else:
                        # Fallback for unassigned text in page body:
                        # Never mark readable text as ART on page body (violates ISO 14289-1 and PAC Quality).
                        cands_fig = [fig for fig in figure_nodes if fig["tag"] != "Artifact" and T._rect_dist(g.cx, g.cy, fig["bbox"]) <= 250.0]
                        cands_text = [m for m in text_nodes if m["id"] not in artifact_nodes and m["tag"] not in ("Artifact", "OBJR", "Lbl")]
                        if cands_fig:
                            best_fig = min(cands_fig, key=lambda fig: T._rect_dist(g.cx, g.cy, fig["bbox"]))
                            glyph_owner[(ui, gi)] = ("node", best_fig["id"])
                            recovered.append(g.uni)
                        elif cands_text:
                            best_n = min(cands_text, key=lambda m: T._rect_dist(g.cx, g.cy, m["bbox"]))
                            glyph_owner[(ui, gi)] = ("node", best_n["id"])
                            (node_invis if invisible else node_vis)[best_n["id"]].append((ui, gi))
                            recovered.append(g.uni)
                        else:
                            glyph_owner[(ui, gi)] = ART
                            orphans.append(g.uni)
                    continue
                if n["id"] in artifact_nodes or n["tag"] == "Artifact":
                    glyph_owner[(ui, gi)] = ART
                    continue
                tok = ("node", n["id"])
                glyph_owner[(ui, gi)] = tok
                (node_invis if invisible else node_vis)[n["id"]].append((ui, gi))

        # Hybrid duplicate guard: a node that has visible native text must not
        # also expose an invisible OCR copy of the same text.
        dup_invisible = 0
        for nid, keys in node_invis.items():
            if node_vis.get(nid):
                for k in keys:
                    glyph_owner[k] = ART
                    dup_invisible += 1

        # Hidden copy of visible text: an invisible glyph (text render mode 3/7,
        # e.g. an OCR or earlier-tagging layer) lying on a visible glyph with the
        # same character is a duplicate, whichever node it was matched to.
        # Invisible text over a scanned image (no visible text below) stays.
        def _gkey(ch):
            k = "".join(c.lower() for c in unicodedata.normalize("NFKD", ch or "") if not unicodedata.combining(c))
            return k.strip()

        cell = 12.0
        visible_grid = defaultdict(list)
        invisible_keys = []
        for ui, u in enumerate(units):
            if u.kind == "text":
                gl = [(g, u.invisible) for g in u.glyphs]
            elif u.kind == "form":
                gl = flatten_glyphs(u)
            else:
                continue
            for gi, (g, invisible) in enumerate(gl):
                k = _gkey(g.uni)
                if not k:
                    continue
                if invisible:
                    invisible_keys.append((ui, gi, g, k))
                else:
                    visible_grid[(int(g.cx // cell), int(g.cy // cell))].append((g, k))
        for ui, gi, g, k in invisible_keys:
            if glyph_owner.get((ui, gi)) == ART:
                continue
            tol = 0.5 * max(g.height, 2.0)
            cx, cy = int(g.cx // cell), int(g.cy // cell)
            hit = False
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for vg, vk in visible_grid.get((cx + dx, cy + dy), ()):
                        if vk == k and abs(vg.cx - g.cx) <= max(tol, 0.5 * vg.height) \
                                and abs(vg.cy - g.cy) <= max(tol, 0.5 * vg.height):
                            hit = True
                            break
                    if hit:
                        break
                if hit:
                    break
            if hit:
                glyph_owner[(ui, gi)] = ART
                dup_invisible += 1

        next_owner_after = {}   # filled right before unit owners are resolved

        def resolve_spaces(ui, n):
            owners = [glyph_owner.get((ui, gi)) for gi in range(n)]
            last = None
            for i, o in enumerate(owners):
                if o is None:
                    owners[i] = last
                else:
                    last = o
            nxt = None
            orig = [glyph_owner.get((ui, gi)) for gi in range(n)]
            for i in range(len(owners) - 1, -1, -1):
                if owners[i] is None:
                    owners[i] = nxt
                else:
                    nxt = owners[i]
            # a space touching a link only stays in the link when the link continues
            # after it -- looking into the NEXT text operator too (leader dots and
            # words are often drawn as one operator each)
            following = next_owner_after.get(ui)
            for i in range(len(owners) - 1, -1, -1):
                if orig[i] is not None:
                    following = orig[i]
                elif owners[i] is not None and owners[i][0] == "link" and following != owners[i]:
                    owners[i] = ("node", owners[i][1])
            return [o if o is not None else ART for o in owners]

        # ------------------------------------------------------------------
        # 3a. LINKS. Every /Link annotation claims the glyphs under its rectangle
        #     (inside whichever node owns them). Plain-text URLs / e-mails found by
        #     InlineLinkRule get exactly one new annotation each. Consecutive
        #     annotations with the same target (a URL broken over two lines) form
        #     ONE Link with several OBJRs.
        # ------------------------------------------------------------------
        glyph_obj_t = {}
        for ui, u in enumerate(units):
            if u.kind == "text":
                for gi, g in enumerate(u.glyphs):
                    glyph_obj_t[(ui, gi)] = g
        node_by_id = {n["id"]: n for n in text_nodes}
        link_annots = T._page_link_annots(page_obj)
        # synthetic annotations for inline links that have none
        created_annots = T._create_inline_link_annots(pdf, page_obj, text_nodes, glyph_owner, glyph_obj_t, link_annots)
        # A link drawn over Artifact content (running header/footer, page number)
        # has nothing to belong to in the structure tree: its annotation object is
        # removed from the page (text and appearance are unchanged).
        removed_artifact_links = 0
        keep = []
        for la in link_annots:
            under = [k for k, g in glyph_obj_t.items()
                     if (g.uni or "").strip() and any(T._inside(g.cx, g.cy, r, 0.6) for r in la["rects"])]
            if under and all(glyph_owner.get(k) in (ART, None) for k in under):
                try:
                    arr = page_obj.Annots
                    for idx in range(len(arr) - 1, -1, -1):
                        if arr[idx].objgen == la["obj"].objgen:
                            del arr[idx]
                    removed_artifact_links += 1
                except Exception:
                    keep.append(la)
                    continue
            else:
                keep.append(la)
        link_annots[:] = keep

        link_groups = {}      # (nid, gid) -> {"annots": [...], "keys": [...], "target": ...}
        node_self_annots = {} # Link-tagged node -> [annot idx]
        glyph_annot = {}
        for key in sorted(glyph_obj_t):
            o = glyph_owner.get(key)
            if not o or o == ART:
                continue
            g = glyph_obj_t[key]
            best, ba = None, None
            for ai, la in enumerate(link_annots):
                if any(T._inside(g.cx, g.cy, r, 0.6) for r in la["rects"]):
                    if ba is None or la["area"] < ba:
                        best, ba = ai, la["area"]
            if best is not None:
                glyph_annot[key] = best
        # group per node in stream order
        cur = {}   # nid -> (gid, target) of the open group
        gid_counter = 0
        for key in sorted(glyph_obj_t):
            o = glyph_owner.get(key)
            if not o or o == ART:
                continue
            nid = o[1]
            ai = glyph_annot.get(key)
            g = glyph_obj_t[key]
            if ai is None:
                if any(c.isalnum() for c in (g.uni or "")):
                    cur.pop(nid, None)      # real text between two links ends the group
                continue
            la = link_annots[ai]
            if (node_by_id.get(nid, {}).get("tag") == "Link"):
                node_self_annots.setdefault(nid, [])
                if ai not in node_self_annots[nid]:
                    node_self_annots[nid].append(ai)
                la["used"] = True
                continue
            open_g = cur.get(nid)
            if open_g is None or open_g[1] != la["target"]:
                gid_counter += 1
                open_g = (gid_counter, la["target"])
                cur[nid] = open_g
                link_groups[(nid, open_g[0])] = {"annots": [], "keys": [], "target": la["target"], "kind": la["kind"]}
            grp = link_groups[(nid, open_g[0])]
            if ai not in grp["annots"]:
                grp["annots"].append(ai)
            grp["keys"].append(key)
            la["used"] = True
            glyph_owner[key] = ("link", nid, open_g[0])
        # Trim link edges: annotation rectangles are often wider than the link
        # ("s.16" for footnote 16, ", http://..."). Leading/trailing punctuation,
        # and for footnote markers the full-size glyphs next to a superscript,
        # go back to the sentence.
        for (nid, gid), grp in list(link_groups.items()):
            keys = sorted(grp["keys"])
            def alnum(k):
                return any(c.isalnum() for c in (glyph_obj_t[k].uni or ""))
            hs = [glyph_obj_t[k].height for k in keys if alnum(k)]
            hmin = min(hs) if hs else 0
            small = [k for k in keys if alnum(k) and glyph_obj_t[k].height <= hmin * 1.15]
            superscript = bool(hs) and len(small) < len([k for k in keys if alnum(k)]) and len(small) <= 4
            def droppable(k, leading):
                g = glyph_obj_t[k]
                u = (g.uni or "").strip()
                if superscript and grp["kind"] == "internal" and k not in small:
                    return True
                if not u:
                    return False
                if u in "*\u2020\u2021\u00A7\u00B6#":
                    return False          # footnote symbols are the link itself
                if leading:
                    return not alnum(k) and u not in "(['\"\u201C"
                return u in ".,;:" and grp["kind"] == "external" or (not alnum(k) and grp["kind"] == "internal" and u in ".,;:")
            orig_keys = list(keys)
            while keys and droppable(keys[0], True):
                keys.pop(0)
            while keys and droppable(keys[-1], False):
                keys.pop()
            if not any(alnum(k) or (glyph_obj_t[k].uni or "").strip() for k in keys):
                keys = orig_keys          # never trim a link down to nothing
            for k in orig_keys:
                if k not in keys:
                    glyph_owner[k] = ("node", nid)
            grp["keys"] = keys

        # One annotation = one Link: when a rectangle spans two entries (two
        # lines of a TOC), it stays with the group that has most glyphs under it.
        share = {}
        for gk, grp in link_groups.items():
            for ai in grp["annots"]:
                n_in = sum(1 for k in grp["keys"] if any(
                    T._inside(glyph_obj_t[k].cx, glyph_obj_t[k].cy, r, 0.6) for r in link_annots[ai]["rects"]))
                share.setdefault(ai, []).append((n_in, gk))
        for ai, lst in share.items():
            if len(lst) > 1:
                lst.sort(key=lambda t: -t[0])
                for _, gk in lst[1:]:
                    link_groups[gk]["annots"].remove(ai)
        for gk in [gk for gk, grp in link_groups.items() if not grp["annots"]]:
            for k in link_groups[gk]["keys"]:
                glyph_owner[k] = ("node", gk[0])
            del link_groups[gk]
        # annotations not owned by any group go through the "unplaced" path later
        owned = {ai for grp in link_groups.values() for ai in grp["annots"]}
        for nid_, ais in node_self_annots.items():
            owned.update(ais)
        for ai, la in enumerate(link_annots):
            la["used"] = ai in owned

        # a second annotation for the same target that covers the same glyphs
        # (duplicates are common) joins the group that owns its area
        for ai, la in enumerate(link_annots):
            if la.get("used"):
                continue
            for (nid, gid), grp in link_groups.items():
                if grp["target"] == la["target"] and any(
                        T._inside(glyph_obj_t[k].cx, glyph_obj_t[k].cy, r, 2.0) for k in grp["keys"] for r in la["rects"]):
                    grp["annots"].append(ai)
                    la["used"] = True
                    break

        # ------------------------------------------------------------------
        # 3b. List labels drawn as graphics (vector dot, numbered circle badge).
        #     Lbl gets the small path/image next to its LBody's first glyph, or
        #     the shape around its own number glyph, so Lbl is never empty.
        # ------------------------------------------------------------------
        glyph_obj = {}
        for ui, u in enumerate(units):
            if u.kind == "text":
                for gi, g in enumerate(u.glyphs):
                    glyph_obj[(ui, gi)] = g
            elif u.kind == "form":
                for gi, (g, _) in enumerate(flatten_glyphs(u)):
                    glyph_obj[(ui, gi)] = g
        label_graphics = {}   # ui -> label node id
        labels = [n for n in text_nodes if n.get("is_label") and n["id"] not in artifact_nodes]
        if labels:
            by_id = {n["id"]: n for n in text_nodes}
            first_glyph = {}
            for key in sorted(glyph_obj):
                o = glyph_owner.get(key)
                if o and o != ART and o[1] not in first_glyph:
                    first_glyph[o[1]] = glyph_obj[key]
            label_boxes = {}
            for key, o in glyph_owner.items():
                if o and o != ART and o[1] in by_id and by_id[o[1]].get("is_label"):
                    gb = glyph_obj[key].bbox
                    lb = label_boxes.get(o[1])
                    label_boxes[o[1]] = gb if lb is None else (min(lb[0], gb[0]), min(lb[1], gb[1]), max(lb[2], gb[2]), max(lb[3], gb[3]))
            graphic_units = [(ui, u) for ui, u in enumerate(units) if u.kind in ("path", "image") and u.bbox]
            for lab in labels:
                cands = []
                if lab["id"] in label_boxes:
                    a_box = label_boxes[lab["id"]]
                    h = max(a_box[3] - a_box[1], 4.0)
                    a_area = max((a_box[2] - a_box[0]) * (a_box[3] - a_box[1]), 1.0)
                    for ui, u in graphic_units:
                        b = u.bbox
                        area = (b[2] - b[0]) * (b[3] - b[1])
                        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
                        if area <= 16 * a_area and T._inside(cx, cy, a_box, max(0.8 * h, 4.0)) \
                                and (b[2] - b[0]) <= 4 * h and (b[3] - b[1]) <= 4 * h:
                            cands.append(ui)
                else:
                    body = None
                    for m in text_nodes:
                        if m["parent"] == lab["parent"] and m is not lab and m["id"] in first_glyph:
                            body = m
                            break
                    g0 = first_glyph.get(body["id"]) if body else None
                    if g0 is not None:
                        h = max(min(g0.height, 40.0), 4.0)
                        for ui, u in graphic_units:
                            b = u.bbox
                            w_, h_ = b[2] - b[0], b[3] - b[1]
                            cy = (b[1] + b[3]) / 2
                            if w_ <= 2.5 * h and h_ <= 2.5 * h and b[2] <= g0.bbox[0] + 1.0 \
                                    and g0.bbox[0] - b[2] <= 3.0 * h and abs(cy - g0.cy) <= 0.8 * h \
                                    and T._inside((b[0] + b[2]) / 2, cy, lab["bbox"], 3.0):
                                cands.append(ui)
                for ui in cands:
                    label_graphics.setdefault(ui, lab["id"])

        # owner of the first non-space glyph AFTER each text operator
        next_owner_after.clear()
        _nxt = None
        for _ui in range(len(units) - 1, -1, -1):
            _u = units[_ui]
            if _u.kind != "text":
                continue
            next_owner_after[_ui] = _nxt
            for _gi in range(len(_u.glyphs)):
                _o = glyph_owner.get((_ui, _gi))
                if _o is not None:
                    _nxt = _o
                    break

        # ------------------------------------------------------------------
        # 3c. Lettering drawn as shapes. A heading / paragraph whose words are
        #     vector outlines (logo titles, display type converted to paths) has
        #     no glyph at all. The shapes inside its box become its content and
        #     the node's text is given as /ActualText (set on the JSON node), so
        #     the tag is never empty and is read correctly.
        # ------------------------------------------------------------------
        nodes_with_glyphs = {o[1] for o in glyph_owner.values() if o and o != ART}
        drawn_text_nodes = [n for n in text_nodes
                            if n["id"] not in nodes_with_glyphs and n["id"] not in artifact_nodes
                            and not n.get("is_label") and n["tag"] not in ("Artifact", "Link", "OBJR", "Lbl")
                            and any(c.isalnum() for c in (n.get("content") or ""))]
        drawn_owner = {}    # ui -> node id
        if drawn_text_nodes:
            for ui, u in enumerate(units):
                if u.kind not in ("path", "image", "form") or not u.bbox:
                    continue
                if u.kind == "form" and flatten_glyphs(u):
                    continue
                best = None
                for n in drawn_text_nodes:
                    b = n["bbox"]
                    pad = max(2.0, 0.15 * (b[3] - b[1]))
                    nb = (b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad)
                    if T._overlap_ratio(u.bbox, nb) >= 0.7:
                        if best is None or n["area"] < best["area"]:
                            best = n
                if best is not None:
                    drawn_owner[ui] = best["id"]
        drawn_used = set(drawn_owner.values())
        if drawn_used and elements:
            def _mark(els):
                for el in els or []:
                    if str(el.get("id")) in drawn_used and not el.get("actual_text"):
                        el["actual_text"] = " ".join((el.get("content") or el.get("text") or "").split())
                    _mark(el.get("kids"))
            _mark(elements)

        # ------------------------------------------------------------------
        # 4. Owner of every unit
        # ------------------------------------------------------------------
        unit_owner = {}     # ui -> token (or list of tokens for split text)
        for ui, u in enumerate(units):
            if u.kind == "text":
                owners = resolve_spaces(ui, len(u.glyphs))
                if not owners:
                    unit_owner[ui] = ART
                elif all(o == owners[0] for o in owners):
                    unit_owner[ui] = owners[0]
                else:
                    unit_owner[ui] = owners
            elif ui in drawn_owner:
                unit_owner[ui] = ("node", drawn_owner[ui])
            elif u.kind == "form":
                gl = flatten_glyphs(u)
                owners = resolve_spaces(ui, len(gl)) if gl else []
                owners = [("node", o[1]) if (o != ART and o[0] == "link") else o for o in owners]
                real = [o for o in owners if o != ART]
                if real:
                    unit_owner[ui] = Counter(real).most_common(1)[0][0]
                else:
                    imgs = flatten_images(u)
                    box = u.bbox
                    f = None
                    for ib in imgs:
                        f = T._figure_for_box(ib, figure_nodes, 0.5)
                        if f:
                            break
                    if f is None and box:
                        f = T._figure_for_box(box, figure_nodes, 0.8)
                    unit_owner[ui] = ("node", f["id"]) if (f and f["tag"] != "Artifact") else ART
            elif ui in label_graphics:
                unit_owner[ui] = ("node", label_graphics[ui])
            elif u.kind == "image":
                f = T._figure_for_box(u.bbox, figure_nodes, 0.5) or T._figure_inside_image(u.bbox, figure_nodes)
                unit_owner[ui] = ("node", f["id"]) if (f and f["tag"] != "Artifact") else ART
            elif u.kind == "path":
                f = T._figure_for_box(u.bbox, figure_nodes, 0.2) if u.bbox else None
                if f is None and u.bbox and figure_nodes:
                    cands = [fig for fig in figure_nodes if fig["tag"] != "Artifact" and (
                        T._inside((u.bbox[0] + u.bbox[2]) / 2, (u.bbox[1] + u.bbox[3]) / 2, fig["bbox"], 12.0) or
                        T._rect_dist((u.bbox[0] + u.bbox[2]) / 2, (u.bbox[1] + u.bbox[3]) / 2, fig["bbox"]) <= 40.0
                    )]
                    if cands:
                        f = min(cands, key=lambda fig: T._rect_dist((u.bbox[0] + u.bbox[2]) / 2, (u.bbox[1] + u.bbox[3]) / 2, fig["bbox"]))
                unit_owner[ui] = ("node", f["id"]) if (f and f["tag"] != "Artifact") else ART
            else:
                unit_owner[ui] = ART

        # ------------------------------------------------------------------
        # 4b. Whitespace-only text operators (e.g. "( ) Tj" between words, very
        #     common in Word/InDesign/LaTeX output) belong to the text around
        #     them. Never artifact them: that would split one heading/paragraph
        #     into a separate marked-content piece per word.
        # ------------------------------------------------------------------
        text_order = [ui for ui, u in enumerate(units) if u.kind == "text"]

        def is_blank(u):
            if not u.glyphs:
                return False
            for g in u.glyphs:
                uni = g.uni or ""
                if any(c.isalnum() or (c.isprintable() and not c.isspace() and ord(c) > 32 and ord(c) not in (0x7F, 0x85, 0xA0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200A, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000)) for c in uni):
                    return False
            return True

        def edge_owner(ui, last):
            o = unit_owner.get(ui)
            if isinstance(o, list):
                return o[-1] if last else o[0]
            return o

        blank_fixed = 0
        for pos, ui in enumerate(text_order):
            u = units[ui]
            if not is_blank(u):
                continue
            prev_o = next_o = None
            prev_u = next_u = None
            for q in range(pos - 1, -1, -1):
                if not is_blank(units[text_order[q]]):
                    prev_o = edge_owner(text_order[q], True)
                    prev_u = units[text_order[q]]
                    break
            for q in range(pos + 1, len(text_order)):
                if not is_blank(units[text_order[q]]):
                    next_o = edge_owner(text_order[q], False)
                    next_u = units[text_order[q]]
                    break

            # Geometry proximity check: only attach blank if it is physically adjacent on the same line
            u_glyphs = u.glyphs
            if not u_glyphs:
                continue
            u_x0 = min(g.bbox[0] for g in u_glyphs)
            u_y0 = min(g.bbox[1] for g in u_glyphs)
            u_x1 = max(g.bbox[2] for g in u_glyphs)
            u_y1 = max(g.bbox[3] for g in u_glyphs)
            u_h = max(max(g.height for g in u_glyphs), 4.0)
            u_ymid = (u_y0 + u_y1) / 2.0

            nearby_prev = False
            if prev_u and prev_u.glyphs:
                p_glyphs = prev_u.glyphs
                p_x1 = max(g.bbox[2] for g in p_glyphs)
                p_ymid = (min(g.bbox[1] for g in p_glyphs) + max(g.bbox[3] for g in p_glyphs)) / 2.0
                p_h = max(max(g.height for g in p_glyphs), 4.0)
                same_line = abs(p_ymid - u_ymid) <= max(4.0, 0.5 * min(u_h, p_h))
                dx = u_x0 - p_x1
                if same_line and (-1.5 <= dx <= max(20.0, 2.0 * min(u_h, p_h))):
                    nearby_prev = True

            nearby_next = False
            if next_u and next_u.glyphs:
                n_glyphs = next_u.glyphs
                n_x0 = min(g.bbox[0] for g in n_glyphs)
                n_ymid = (min(g.bbox[1] for g in n_glyphs) + max(g.bbox[3] for g in n_glyphs)) / 2.0
                n_h = max(max(g.height for g in n_glyphs), 4.0)
                same_line = abs(n_ymid - u_ymid) <= max(4.0, 0.5 * min(u_h, n_h))
                dx = n_x0 - u_x1
                if same_line and (-1.5 <= dx <= max(20.0, 2.0 * min(u_h, n_h))):
                    nearby_next = True

            if prev_o is not None and prev_o != ART and prev_o[0] == "link" and next_o != prev_o and nearby_prev:
                new_o = ("node", prev_o[1])  # space after a link belongs to the sentence
            elif prev_o is not None and prev_o != ART and nearby_prev:
                # Do not attach trailing whitespace to a heading (headings only enclose their actual text)
                if prev_o[0] == "node" and str(node_id_to_tag.get(prev_o[1], "")).startswith("H"):
                    new_o = ART
                else:
                    new_o = prev_o            # trailing space of the previous word on the same line
            elif next_o is not None and next_o != ART and nearby_next:
                # Do not attach leading whitespace to a heading
                if next_o[0] == "node" and str(node_id_to_tag.get(next_o[1], "")).startswith("H"):
                    new_o = ART
                else:
                    new_o = next_o            # leading space of the next word on the same line
            else:
                new_o = ART               # isolated stray whitespace is an artifact
            if new_o != unit_owner.get(ui):
                unit_owner[ui] = new_o
                blank_fixed += 1

        # ------------------------------------------------------------------
        # 5. Emit the stream with BDC/EMC
        # ------------------------------------------------------------------
        unit_at = {u.start: (ui, u) for ui, u in enumerate(units)}
        out = []
        tagged_nodes = {}
        mcid = mcid_start
        open_owner = [None]

        def close():
            if open_owner[0] is not None:
                out.append(ContentStreamInstruction([], Operator("EMC")))
                open_owner[0] = None

        def ensure(owner):
            nonlocal mcid
            if open_owner[0] == owner:
                return
            close()
            if owner == ART:
                out.append(ContentStreamInstruction([
                    Name("/Artifact"),
                    Dictionary(Type=Name("/Layout"), Subtype=Name("/Layout"))
                ], Operator("BDC")))
            elif owner[0] == "link":
                out.append(ContentStreamInstruction([Name("/Link"), Dictionary(MCID=mcid)], Operator("BDC")))
                link_groups[(owner[1], owner[2])].setdefault("mcids", []).append(mcid)
                tagged_nodes.setdefault(owner[1], [])
                mcid += 1
            else:
                nid = owner[1]
                tag = node_id_to_tag.get(nid, "P") or "P"
                if tag in ("Artifact", "OBJR"):
                    tag = "Span"
                out.append(ContentStreamInstruction([Name("/" + tag), Dictionary(MCID=mcid)], Operator("BDC")))
                tagged_nodes.setdefault(nid, []).append(mcid)
                mcid += 1
            open_owner[0] = owner

        # Block-aware emission. Word/InDesign wrap every word run in its own
        # "q ... BT ... ET Q". A q..Q or BT..ET block whose content all belongs to
        # one owner is emitted as a whole INSIDE one marked-content sequence, so a
        # paragraph/link is one piece instead of one piece per word. Blocks with
        # mixed owners are entered and marked unit by unit. Sequences are always
        # opened and closed at the same nesting level (valid q/Q, BT/ET nesting).
        n_inst = len(instructions)
        split_ops = 0
        match = {}
        q_stack, bt_open = [], None
        for idx, inst in enumerate(instructions):
            if isinstance(inst, pikepdf.ContentStreamInlineImage):
                continue
            op = str(inst.operator)
            if op == "q":
                q_stack.append(idx)
            elif op == "Q" and q_stack:
                match[q_stack.pop()] = idx
            elif op == "BT":
                bt_open = idx
            elif op == "ET" and bt_open is not None:
                match[bt_open] = idx
                bt_open = None
        unit_starts = sorted(unit_at)
        import bisect
        EMPTY = ("empty",)
        MIXED = ("mixed",)

        def block_owner(a, b):
            lo = bisect.bisect_left(unit_starts, a)
            hi = bisect.bisect_right(unit_starts, b)
            owner = EMPTY
            for st in unit_starts[lo:hi]:
                o = unit_owner.get(unit_at[st][0], ART)
                if isinstance(o, list):
                    return MIXED
                if owner is EMPTY:
                    owner = o
                elif o != owner:
                    return MIXED
            return owner

        def block_has_non_blank(a, b):
            lo = bisect.bisect_left(unit_starts, a)
            hi = bisect.bisect_right(unit_starts, b)
            for st in unit_starts[lo:hi]:
                u = unit_at[st][1]
                if u.kind != "text" or not is_blank(u):
                    return True
            return False

        def emit_range(a, b):
            nonlocal split_ops
            i = a
            while i <= b:
                inst = instructions[i]
                if not isinstance(inst, pikepdf.ContentStreamInlineImage):
                    op = str(inst.operator)
                    if op in ("q", "BT") and i in match and match[i] <= b:
                        j = match[i]
                        bo = block_owner(i, j)
                        is_heading_block = (bo not in (EMPTY, MIXED, ART) and bo[0] == "node" and str(node_id_to_tag.get(bo[1], "")).startswith("H"))
                        if bo is EMPTY:
                            out.extend(instructions[i:j + 1])     # clip / state only
                        elif bo is not MIXED and not (op == "q" and is_heading_block):
                            ensure(bo)
                            out.extend(instructions[i:j + 1])
                        else:
                            close()
                            out.append(inst)
                            emit_range(i + 1, j - 1)
                            close()
                            out.append(instructions[j])
                        i = j + 1
                        continue
                if i in unit_at:
                    ui, u = unit_at[i]
                    owner = unit_owner.get(ui, ART)
                    if isinstance(owner, list):
                        split_ops += 1
                        for piece_owner, piece_insts in T._split_text_instruction(inst, u.glyphs, owner):
                            ensure(piece_owner if piece_owner is not None else ART)
                            out.extend(piece_insts)
                        i += 1
                        continue
                    if u.kind == "text" and is_blank(u):
                        if open_owner[0] is not None and open_owner[0] != ART:
                            out.extend(instructions[u.start:u.end + 1])
                            i = u.end + 1
                            continue
                        ensure(ART)
                        out.extend(instructions[u.start:u.end + 1])
                        close()
                        i = u.end + 1
                        continue
                    ensure(owner)
                    out.extend(instructions[u.start:u.end + 1])
                    i = u.end + 1
                    continue
                if not isinstance(inst, pikepdf.ContentStreamInlineImage):
                    if str(inst.operator) in ("q", "Q", "BT", "ET", "BDC", "BMC", "EMC"):
                        close()   # unmatched block boundary / kept /OC sequence
                    elif open_owner[0] is None and str(inst.operator) in ('Tj', 'TJ', "'", '"', 'Do', 'm', 'l', 'c', 'v', 'y', 'h', 're', 'S', 's', 'f', 'F', 'f*', 'B', 'B*', 'b', 'b*', 'sh'):
                        ensure(ART)
                out.append(inst)
                i += 1

        emit_range(0, n_inst - 1)
        close()

        # ------------------------------------------------------------------
        # 5b. Link results -> per node items (ints + link groups, stream order)
        # ------------------------------------------------------------------
        def glyph_text(keys):
            return "".join(glyph_obj_t[k].uni or "" for k in sorted(keys))

        # glyph text including inner spaces of the group (from first to last key)
        def span_text(keys):
            """Visible text of the glyphs, with a space wherever the PDF moved
            to the next word or line without drawing a space character."""
            if not keys:
                return ""
            ks = sorted(keys)
            first, last = ks[0], ks[-1]
            out, prev = [], None
            for k in sorted(glyph_obj_t):
                if not (first <= k <= last):
                    continue
                g = glyph_obj_t[k]
                if prev is not None and g.uni and g.uni.strip():
                    h = max(min(g.height, prev.height), 1.0)
                    new_line = abs(g.cy - prev.cy) > 0.5 * h
                    gap = g.bbox[0] - prev.bbox[2] > 0.18 * h
                    if (new_line or gap) and out and not out[-1].endswith(" "):
                        out.append(" ")
                out.append(g.uni or "")
                prev = g
            return "".join(out)

        unplaced = [ai for ai, la in enumerate(link_annots) if not la.get("used")]
        for ai in unplaced:
            la = link_annots[ai]
            r = la["rects"][0]
            cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
            holders = [n for n in text_nodes if T._inside(cx, cy, n["bbox"], 2.0) and n["id"] in tagged_nodes]
            holder = min(holders, key=lambda n: n["area"]) if holders else None
            if holder is None:
                tn = [n for n in text_nodes if n["id"] in tagged_nodes]
                holder = min(tn, key=lambda n: T._rect_dist(cx, cy, n["bbox"])) if tn else None
            if holder is None:
                continue
            if holder["tag"] == "Link":
                node_self_annots.setdefault(holder["id"], []).append(ai)
            else:
                gid_counter += 1
                link_groups[(holder["id"], gid_counter)] = {"annots": [ai], "keys": [], "target": la["target"], "kind": la["kind"], "mcids": []}

        result = {}
        for nid, mcids in tagged_nodes.items():
            result[nid] = list(mcids)
        for (nid, gid), grp in link_groups.items():
            text = span_text(grp["keys"]) if grp["keys"] else ""
            annots = [link_annots[a]["obj"] for a in grp["annots"]]
            node = node_by_id.get(nid, {})
            is_ref = (grp["kind"] == "internal" and node.get("tag") not in ("Reference", "TOCI", "Link", "Lbl")
                      and T._looks_like_note_ref(text))
            item = {"type": "LinkGroup", "mcids": list(grp.get("mcids", [])), "annots": annots,
                    "alt": T.link_alt(text, link_annots[grp["annots"][0]]), "reference": is_ref,
                    "kind": grp["kind"]}
            item["mcid"] = item["mcids"][0] if item["mcids"] else None
            result.setdefault(nid, []).append(item)
        for nid, ais in node_self_annots.items():
            if nid not in result:
                continue
            keys = [k for k, o in glyph_owner.items() if o == ("node", nid) and k in glyph_obj_t]
            result[nid].append({"type": "LinkObjr", "annots": [link_annots[a]["obj"] for a in ais],
                                "alt": T.link_alt(span_text(keys), link_annots[ais[0]]), "mcid": None})
        # keep stream order: ints and groups sorted by their first MCID, OBJR-only last
        for nid in result:
            def k(it):
                if isinstance(it, int):
                    return it
                return it["mcid"] if it.get("mcid") is not None else 10 ** 9
            result[nid].sort(key=k)
        tagged_nodes_out = result

        # ------------------------------------------------------------------
        # 6. Report
        # ------------------------------------------------------------------
        tagged_ids = set(tagged_nodes)
        # A parent whose text is carried by a tagged child (TOCI > Reference) is covered.
        covered_ids = set(tagged_ids)
        for n in text_nodes:
            if n["id"] in tagged_ids:
                covered_ids.update(n["ancestors"])
        missing = [n for n in text_nodes
                   if n["id"] not in covered_ids and n["id"] not in artifact_nodes and n["tag"] != "Artifact"]
        raster_boxes = [u.bbox for u in units if u.kind == "image" and u.bbox]
        for u in units:
            if u.kind == "form":
                raster_boxes.extend(flatten_images(u))

        def on_raster(n):
            b = n["bbox"]
            cxn, cyn = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            return any(T._inside(cxn, cyn, rb, 0) for rb in raster_boxes)

        coverage = {}
        for n in text_nodes:
            if n["id"] in tagged_ids:
                used = n["total"] - sum(v for v in n["remaining"].values() if v > 0)
                coverage[n["id"]] = round(used / n["total"], 3)

        report = {
            "units": dict(Counter(u.kind for u in units)),
            "glyphs": sum(len(u.glyphs) for u in units if u.kind == "text") + sum(len(flatten_glyphs(u)) for u in units if u.kind == "form"),
            "orphan_glyphs": len(orphans),
            "orphan_text": "".join(orphans)[:200],
            "recovered_into_containers": "".join(recovered)[:200],
            "invisible_duplicates_artifacted": dup_invisible,
            "split_text_ops": split_ops,
            "link_annotations": len(link_annots),
            "links_tagged": len(link_groups) + sum(len(v) for v in node_self_annots.values()),
            "link_annotations_created_for_plain_urls": created_annots,
            "link_annotations_removed_on_artifacts": removed_artifact_links,
            "blank_ops_joined_to_text": blank_fixed,
            "ops_placed_by_sequence": anchored_units,
            "nodes_total": len(text_nodes),
            "nodes_tagged": len([n for n in text_nodes if n["id"] in covered_ids]),
            "nodes_artifact": len([n for n in text_nodes if n["id"] in artifact_nodes or n["tag"] == "Artifact"]),
            "figures_tagged": len([f for f in figure_nodes if f["id"] in tagged_ids]),
            "figures_total": len(figure_nodes),
            "nodes_without_native_text": [{"id": n["id"], "content": n["content"][:60], "on_raster": on_raster(n)} for n in missing],
            "low_coverage_nodes": {k: v for k, v in coverage.items() if v < 0.9},
            "labels_from_graphics": sorted(set(label_graphics.values())),
        }
        T.last_report = report
        T.raster_fallback_nodes = {n["id"] for n in missing if on_raster(n)}
        print(f"[DigitalStreamTagger] nodes {report['nodes_tagged']}/{report['nodes_total']} tagged "
              f"({report['nodes_artifact']} artifact by JSON), "
              f"figures {report['figures_tagged']}/{report['figures_total']}, glyphs {report['glyphs']}, "
              f"orphans {report['orphan_glyphs']}, split ops {split_ops}, untagged nodes {len(missing)}")
        return out, tagged_nodes_out, mcid


DigitalStreamTagger.raster_fallback_nodes = set()
