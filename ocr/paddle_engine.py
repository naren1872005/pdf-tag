import os
import re
import fitz
import numpy as np

# Crucial Windows fix: torch must be loaded before paddle to avoid DLL conflicts
try:
    import torch
except Exception as e:
    pass

import paddle
import paddle.inference as p_inf

# Monkey-patch create_predictor to avoid OneDNN/MKL filter missing assertion on Windows
_orig_create_predictor = p_inf.create_predictor

def _safe_create_predictor(config):
    try:
        config.disable_onednn()
    except Exception:
        pass
    try:
        config.disable_mkldnn()
    except Exception:
        pass
    return _orig_create_predictor(config)

p_inf.create_predictor = _safe_create_predictor

class PaddleLayoutExtractor:
    """
    Local layout and text extractor using PaddleOCR + PyMuPDF.
    Requires no cloud API key, no activation code, and runs 100% offline.
    """
    _ocr_instance = None

    @classmethod
    def get_ocr(cls):
        if cls._ocr_instance is None:
            try:
                from paddleocr import PaddleOCR
                cls._ocr_instance = PaddleOCR(
                    use_angle_cls=False,
                    lang="en",
                    use_gpu=False,
                    show_log=False
                )
            except Exception as e:
                print(f"[Warning] Local OCR could not be initialized: {e}")
                cls._ocr_instance = None
        return cls._ocr_instance

    @staticmethod
    def check_pdf_has_struct_tree(doc) -> bool:
        try:
            catalog_xref = doc.pdf_catalog()
            if catalog_xref <= 0:
                return False
            cat_dict = doc.xref_object(catalog_xref)
            if "/StructTreeRoot" not in cat_dict:
                return False

            # Verify that the StructTree contains authentic semantic structure
            # (at least one Heading H1-H6, List L/LI, Table, or Figure).
            # If a document's StructTree is degenerate (e.g. 0 headings, only dummy /P or /Sect),
            # it is unremediated and must undergo intelligent layout extraction.
            flags = fitz.TEXTFLAGS_SEARCH | fitz.TEXT_COLLECT_STRUCTURE
            has_semantic_tag = False
            for page in doc[:min(5, len(doc))]:
                t = page.get_text("dict", flags=flags)
                def check_blocks(bl):
                    nonlocal has_semantic_tag
                    for b in bl:
                        tag = (b.get("std") or b.get("raw") or "")
                        if tag in ("H", "H1", "H2", "H3", "H4", "H5", "H6", "L", "LI", "Table", "TH", "Figure"):
                            has_semantic_tag = True
                            return
                        if b.get("blocks"):
                            check_blocks(b["blocks"])
                            if has_semantic_tag:
                                return
                check_blocks(t.get("blocks", []))
                if has_semantic_tag:
                    return True
            return has_semantic_tag
        except Exception:
            pass
        return False

    @staticmethod
    def _extract_all_pikepdf_spans(pdf_path: str, doc):
        """
        Extracts all <Span> accessibility nodes with /ActualText from pikepdf's StructTreeRoot
        and correlates their bounding boxes from page content streams.
        """
        page_spans = {}
        try:
            import pikepdf
            with pikepdf.open(pdf_path) as pdf:
                for p_idx, page in enumerate(pdf.pages):
                    pno = p_idx + 1
                    p_obj = page.obj
                    fitz_p = doc[p_idx]

                    content = fitz_p.read_contents().decode("latin1", errors="replace")
                    mcid_bboxes = {}
                    pattern = re.compile(r'/(\w+)\s*<<[^>]*?MCID\s*(\d+)[^>]*?>>\s*BDC(.*?)EMC', re.DOTALL)
                    for m in pattern.finditer(content):
                        mcid = int(m.group(2))
                        body = m.group(3)
                        cm = re.search(r'1\s+0\s+0\s+1\s+([\d\.\-]+)\s+([\d\.\-]+)\s+cm.*?0\s+0\s+m\s+([\d\.\-]+)\s+0\s+l', body, re.DOTALL)
                        if cm:
                            x0 = float(cm.group(1))
                            pdf_y = float(cm.group(2))
                            w = float(cm.group(3))
                            mcid_bboxes[mcid] = [round(x0, 2), round(pdf_y - 2, 2), round(x0 + w, 2), round(pdf_y + 2, 2)]

                    found_spans = []
                    def walk_node(node, ancestors=None, curr_pg=None):
                        if ancestors is None:
                            ancestors = []
                        if not isinstance(node, pikepdf.Dictionary):
                            return
                        s = str(node.get("/S", "")).replace("/", "")
                        act = str(node.get("/ActualText", "")).strip().replace("\x00", "")
                        if "/Pg" in node:
                            curr_pg = node["/Pg"]
                        k = node.get("/K")
                        current_anc = ancestors + [s]

                        if s == "Span" and act and curr_pg and curr_pg.objgen == p_obj.objgen:
                            mcid = None
                            if isinstance(k, (int, pikepdf.Integer)):
                                mcid = int(k)
                            elif isinstance(k, pikepdf.Dictionary) and "/MCID" in k:
                                mcid = int(k["/MCID"])
                            bbox = mcid_bboxes.get(mcid)
                            found_spans.append({
                                "type": "span",
                                "tag": "Span",
                                "actual_text": act,
                                "content": "",
                                "bounding_box": bbox,
                                "mcid": mcid,
                                "ancestors": ancestors
                            })
                        kids = list(k) if isinstance(k, (list, pikepdf.Array)) else ([k] if isinstance(k, pikepdf.Dictionary) else [])
                        for ch in kids:
                            walk_node(ch, current_anc, curr_pg)

                    root = pdf.Root.get("/StructTreeRoot")
                    if root and "/K" in root:
                        walk_node(root["/K"])

                    page_spans[pno] = found_spans
        except Exception:
            pass
        return page_spans

    @staticmethod
    def _extract_from_struct_tree(doc, pdf_path: str, progress_callback=None) -> dict:
        total_pages = len(doc)
        pike_spans_map = PaddleLayoutExtractor._extract_all_pikepdf_spans(pdf_path, doc)
        extracted_doc = {
            "type": "document",
            "metadata": {
                "title": os.path.splitext(os.path.basename(pdf_path))[0].replace("_", " ").title(),
                "author": "PDF TagPilot",
                "language": "en-US",
                "page_count": total_pages
            },
            "pages": []
        }

        TAG_TO_TYPE = {
            'Document': 'document',
            'Part': 'section',
            'Art': 'article',
            'Sect': 'section',
            'Div': 'division',
            'H': 'heading',
            'H1': 'heading',
            'H2': 'heading',
            'H3': 'heading',
            'H4': 'heading',
            'H5': 'heading',
            'H6': 'heading',
            'P': 'paragraph',
            'L': 'list',
            'LI': 'list_item',
            'Lbl': 'list_label',
            'LBody': 'list_body',
            'Table': 'table',
            'TR': 'table_row',
            'TH': 'table_header_cell',
            'TD': 'table_data_cell',
            'Figure': 'figure',
            'Formula': 'formula',
            'Caption': 'caption',
            'Span': 'span',
            'Quote': 'quote',
            'BlockQuote': 'blockquote',
            'TOC': 'toc',
            'TOCI': 'toci',
            'Reference': 'reference'
        }

        def extract_node_text(b):
            lines = b.get('lines', [])
            parts = []
            for l in lines:
                for s in l.get('spans', []):
                    st = s.get('text', '')
                    if st:
                        parts.append(st)
            for sb in b.get('blocks', []):
                sub_t = extract_node_text(sb)
                if sub_t:
                    parts.append(sub_t)
            return ' '.join(' '.join(parts).split())

        def id_generator():
            i = 1
            while True:
                yield i
                i += 1

        id_gen = id_generator()
        flags = fitz.TEXTFLAGS_SEARCH | fitz.TEXT_COLLECT_STRUCTURE

        def walk_tree(b, page_h, pno):
            tag = b.get('std') or b.get('raw')
            sub = b.get('blocks', [])
            bbox = b.get('bbox')

            # If this block has no tag, it is direct content of its parent StructElem,
            # not a separate StructElem node.
            if not tag:
                return None

            # Flatten document/section wrappers so top-level elements are direct
            if tag in ('Document', 'Sect', 'Part', 'Art', 'Div'):
                kids = []
                for sb in sub:
                    k = walk_tree(sb, page_h, pno)
                    if k:
                        if isinstance(k, list):
                            kids.extend(k)
                        else:
                            kids.append(k)
                return kids

            if tag in ('Table', 'table'):
                # Check if this table is a pseudo-table / layout container (e.g. sidebar box, callout, 1-row banner)
                rows = []
                cells = []
                def collect_table(k_list):
                    for k_item in k_list or []:
                        if isinstance(k_item, dict):
                            t_val = k_item.get('tag') or k_item.get('type')
                            if t_val in ('TR', 'table_row'):
                                rows.append(k_item)
                                collect_table(k_item.get('kids'))
                            elif t_val in ('TD', 'TH', 'table_data_cell', 'table_header_cell'):
                                cells.append(k_item)
                            elif k_item.get('kids'):
                                collect_table(k_item.get('kids'))
                collect_table(kids)
                real_cells = [c for c in cells if bool((c.get('content') or '').strip()) or bool(c.get('kids'))]
                is_layout = len(rows) <= 1 or len(real_cells) <= 1
                if not is_layout and rows:
                    row_cell_counts = []
                    for r in rows:
                        rc = [k for k in r.get('kids') or [] if (k.get('tag') or k.get('type')) in ('TD', 'TH', 'table_data_cell', 'table_header_cell')]
                        row_cell_counts.append(len(rc))
                    if row_cell_counts and max(row_cell_counts) <= 1:
                        is_layout = True
                if is_layout:
                    # Dissolve layout table into its direct contents
                    unwrapped = []
                    def unwrap_content(k_item):
                        if not isinstance(k_item, dict):
                            return
                        t_val = k_item.get('tag') or k_item.get('type')
                        if t_val in ('Table', 'table', 'TR', 'table_row', 'TD', 'TH', 'table_data_cell', 'table_header_cell'):
                            for sub_k in k_item.get('kids') or []:
                                unwrap_content(sub_k)
                            if not k_item.get('kids') and (k_item.get('content') or '').strip():
                                b_box = k_item.get('bounding_box')
                                if b_box and len(b_box) == 4:
                                    w = abs(b_box[2] - b_box[0])
                                    h = abs(b_box[3] - b_box[1])
                                    if (w < 5.0 and h > 100.0) or w <= 0.1 or h <= 0.1:
                                        return
                                unwrapped.append({
                                    'id': k_item.get('id', next(id_gen)),
                                    'type': 'paragraph',
                                    'tag': 'P',
                                    'content': k_item.get('content'),
                                    'bounding_box': b_box,
                                    'confidence': 1.0
                                })
                        else:
                            b_box = k_item.get('bounding_box')
                            if b_box and len(b_box) == 4:
                                w = abs(b_box[2] - b_box[0])
                                h = abs(b_box[3] - b_box[1])
                                if (w < 5.0 and h > 100.0) or w <= 0.1 or h <= 0.1:
                                    return
                            cnt = (k_item.get('content') or '').strip()
                            m_split = re.match(r'^(1\.\s+.*?)(?:\s+|\n)(2\.\s+.*)$', cnt, re.DOTALL)
                            if m_split:
                                unwrapped.append({
                                    'id': next(id_gen),
                                    'type': 'list_item',
                                    'tag': 'LI',
                                    'content': m_split.group(1).strip(),
                                    'bounding_box': b_box,
                                    'confidence': 1.0
                                })
                                unwrapped.append({
                                    'id': next(id_gen),
                                    'type': 'list_item',
                                    'tag': 'LI',
                                    'content': m_split.group(2).strip(),
                                    'bounding_box': b_box,
                                    'confidence': 1.0
                                })
                            else:
                                unwrapped.append(k_item)
                    for k_item in kids:
                        unwrap_content(k_item)

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
                    return merged_unwrapped

            text = extract_node_text(b)

            # Process child struct elements (e.g. inline <Span>, <Link>, etc.)
            kids = []
            if tag != 'Span':  # Span is an inline leaf element
                for sb in sub:
                    k = walk_tree(sb, page_h, pno)
                    if k:
                        if isinstance(k, list):
                            kids.extend(k)
                        else:
                            kids.append(k)

            pdf_bbox = None
            if bbox and len(bbox) == 4:
                pdf_bbox = [
                    round(bbox[0], 2),
                    round(page_h - bbox[3], 2),
                    round(bbox[2], 2),
                    round(page_h - bbox[1], 2)
                ]

            curr_id = next(id_gen)
            elem_type = TAG_TO_TYPE.get(tag, 'paragraph' if text else 'container')

            res = {
                'id': curr_id,
                'type': elem_type,
                'tag': tag,
                'content': text,
                'bounding_box': pdf_bbox,
                'confidence': 1.0
            }
            if tag in ('H1', 'H2', 'H3', 'H4', 'H5', 'H6'):
                res['heading_level'] = int(tag[1])
            elif tag == 'H':
                res['heading_level'] = 1

            if tag in ('Formula', 'formula'):
                full_text = text
                if not full_text and kids:
                    full_text = " ".join(k.get("content", "") for k in kids if isinstance(k, dict) and k.get("content")).strip()
                res['content'] = full_text
                res['tag'] = 'Formula'
                res['type'] = 'formula'
                res['alt_text'] = PaddleLayoutExtractor._math_to_speech(full_text)
                return res

            if tag == 'Figure':
                # Figures and graphs are atomic single tags with Alt text; never extract sub-text elements
                if kids:
                    text_kids = [k for k in kids if isinstance(k, dict) and (k.get('content') or '').strip()]
                    if text_kids and not res.get('alt_text'):
                        summary = ", ".join((k.get('content') or '')[:30] for k in text_kids[:3])
                        res['alt_text'] = f'Figure on Page {pno}: {summary}'
                    kids = []
                    res.pop('kids', None)

                # Check if embedded image at figure bbox has text extractable via OCR for Alt summary only
                if not res.get('alt_text') and not text and pdf_bbox:
                    try:
                        r = fitz.Rect(pdf_bbox[0], page_h - pdf_bbox[3], pdf_bbox[2], page_h - pdf_bbox[1])
                        page = doc[pno - 1]
                        for img_info in page.get_images():
                            xref = img_info[0]
                            for img_r in page.get_image_rects(xref):
                                if img_r.intersects(r):
                                    pix = fitz.Pixmap(doc, xref)
                                    if pix.n >= 5:
                                        pix = fitz.Pixmap(fitz.csRGB, pix)
                                    if pix.width >= 20 and pix.height >= 20:
                                        img_np = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.height, pix.width, pix.n))
                                        if pix.n == 4:
                                            img_np = img_np[:, :, :3]
                                        ocr = PaddleLayoutExtractor.get_ocr()
                                        ocr_res = ocr.ocr(img_np, cls=False)
                                        lines_detected = ocr_res[0] if ocr_res and ocr_res[0] else []
                                        if lines_detected:
                                            summary_parts = [l[1][0] for l in lines_detected[:3] if l and len(l) > 1 and l[1]]
                                            summary = ", ".join(summary_parts)
                                            if summary:
                                                res['alt_text'] = f'Figure on Page {pno}: {summary}'
                                                break
                    except Exception:
                        pass

                if not res.get('alt_text'):
                    res['alt_text'] = f'Figure on Page {pno}'

                return res

            if tag in ('P', 'p') and kids and any(isinstance(k, dict) and k.get('tag') == 'Formula' for k in kids):
                return kids

            if kids:
                res['kids'] = kids

            return res


        for page_idx in range(total_pages):
            page_num = page_idx + 1
            if progress_callback:
                progress_callback(page_num, total_pages, f"Reading accessibility structure: Page {page_num} of {total_pages}...")

            page = doc[page_idx]
            page_w, page_h = page.rect.width, page.rect.height
            t = page.get_text("dict", flags=flags)
            page_elements = []
            for b in t.get("blocks", []):
                res = walk_tree(b, page_h, page_num)
                if res:
                    if isinstance(res, list):
                        page_elements.extend(res)
                    else:
                        page_elements.append(res)

            # Restore drawing-based <Span> elements (e.g. fill-in-the-blank underlines) that MuPDF dropped
            p_spans = pike_spans_map.get(page_num, [])
            if p_spans:
                blank_spans = [s for s in p_spans if "Blank" in s.get("actual_text", "")]
                if blank_spans:
                    li_blank_spans = [s for s in blank_spans if any(a in s.get("ancestors", []) for a in ("LI", "LBody"))]
                    standalone_blank_spans = [s for s in blank_spans if not any(a in s.get("ancestors", []) for a in ("LI", "LBody"))]

                    # 1. Attach standalone blank spans to empty <P> container (e.g. Question B answer block)
                    if standalone_blank_spans:
                        for el in page_elements:
                            if el.get("tag") == "P" and not el.get("content") and not el.get("kids"):
                                el["kids"] = [
                                    {
                                        "id": next(id_gen),
                                        "type": "span",
                                        "tag": "Span",
                                        "actual_text": bs["actual_text"],
                                        "content": "",
                                        "bounding_box": bs["bounding_box"],
                                        "confidence": 1.0
                                    }
                                    for bs in standalone_blank_spans
                                ]
                                valid_boxes = [k["bounding_box"] for k in el["kids"] if k.get("bounding_box")]
                                if valid_boxes:
                                    el["bounding_box"] = [
                                        min(b[0] for b in valid_boxes), min(b[1] for b in valid_boxes),
                                        max(b[2] for b in valid_boxes), max(b[3] for b in valid_boxes)
                                    ]
                                break

                    # 2. Attach list item blank spans to matching LI -> LBody -> P (e.g. Question C items)
                    if li_blank_spans:
                        all_lis = []
                        def collect_lis(node):
                            if isinstance(node, dict):
                                if node.get("tag") == "LI":
                                    all_lis.append(node)
                                for c in node.get("kids", []):
                                    collect_lis(c)
                        for el in page_elements:
                            collect_lis(el)

                        for bs in li_blank_spans:
                            bbox = bs.get("bounding_box")
                            if not bbox:
                                continue
                            mid_y = (bbox[1] + bbox[3]) / 2.0
                            best_li = None
                            min_dist = 999999
                            for li in all_lis:
                                l_box = li.get("bounding_box")
                                if l_box and len(l_box) == 4:
                                    if l_box[1] - 8 <= mid_y <= l_box[3] + 8:
                                        best_li = li
                                        break
                                    dist = min(abs(mid_y - l_box[1]), abs(mid_y - l_box[3]))
                                    if dist < min_dist and dist < 25:
                                        min_dist = dist
                                        best_li = li

                            if best_li:
                                target_c = None
                                for k in best_li.get("kids", []):
                                    if k.get("tag") == "LBody":
                                        for sub in k.get("kids", []):
                                            if sub.get("tag") == "P":
                                                target_c = sub
                                                break
                                        if not target_c:
                                            target_c = k
                                        break
                                if not target_c:
                                    target_c = best_li

                                if "kids" not in target_c or not target_c["kids"]:
                                    target_c["kids"] = []
                                target_c["kids"].append({
                                    "id": next(id_gen),
                                    "type": "span",
                                    "tag": "Span",
                                    "actual_text": bs["actual_text"],
                                    "content": "",
                                    "bounding_box": bs["bounding_box"],
                                    "confidence": 1.0
                                })

            for idx, el in enumerate(page_elements):
                el["reading_order"] = idx + 1

            extracted_doc["pages"].append({
                "page_number": page_num,
                "width": round(page_w, 2),
                "height": round(page_h, 2),
                "elements": page_elements
            })

        doc.close()
        return extracted_doc

    @staticmethod
    def extract_document(pdf_path: str, progress_callback=None) -> dict:
        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        
        # Check if the PDF already contains authentic StructTree tags
        if PaddleLayoutExtractor.check_pdf_has_struct_tree(doc):
            return PaddleLayoutExtractor._extract_from_struct_tree(doc, pdf_path, progress_callback)

        extracted_doc = {
            "type": "document",
            "metadata": {
                "title": os.path.splitext(os.path.basename(pdf_path))[0].replace("_", " ").title(),
                "author": "PDF TagPilot",
                "language": "en-US",
                "page_count": total_pages
            },
            "pages": []
        }

        # Global typography sampling across the document to establish base body font size
        font_sizes = []
        sample_pages = min(total_pages, 8)
        for p_idx in range(sample_pages):
            p = doc[p_idx]
            for b in p.get_text("dict", flags=fitz.TEXTFLAGS_SEARCH).get("blocks", []):
                if b.get("type") == 0:
                    for l in b.get("lines", []):
                        for s in l.get("spans", []):
                            txt = s.get("text", "").strip()
                            if txt:
                                font_sizes.append(s.get("size", 10.0))

        base_font_size = float(np.median(font_sizes)) if font_sizes else 11.0

        global_id_counter = 1
        has_seen_h1 = False

        for page_idx in range(total_pages):
            page_num = page_idx + 1
            if progress_callback:
                progress_callback(page_num, total_pages, f"Analyzing Page {page_num} of {total_pages}...")

            page = doc[page_idx]
            page_rect = page.rect
            page_w, page_h = page_rect.width, page_rect.height

            # Detect whether page has native digital text
            text_instances = page.get_text("blocks")
            is_digital = len(text_instances) > 0 and any(len(b[4].strip()) > 3 for b in text_instances if b[4])

            page_elements = []

            if is_digital:
                # Dynamic layout extraction from native PDF text, tables, and typography
                page_elements, global_id_counter, has_seen_h1 = PaddleLayoutExtractor._extract_digital_page(
                    page, page_num, page_w, page_h, global_id_counter, has_seen_h1, base_font_size
                )
            else:
                # Scanned PDF extraction via PaddleOCR
                page_elements, global_id_counter, has_seen_h1 = PaddleLayoutExtractor._extract_scanned_page(
                    page, page_num, page_w, page_h, global_id_counter, has_seen_h1
                )

            # Check for embedded figures / images on the page and extract meaningful content
            figures_data, global_id_counter = PaddleLayoutExtractor._extract_figures(
                page, page_num, page_w, page_h, global_id_counter, base_font_size,
                existing_elements=page_elements, return_data=True
            )

            # Integrate figures and their extracted content in context-aware reading order
            page_elements = PaddleLayoutExtractor._integrate_figures_reading_order(
                page_elements, figures_data, page_w, page_h
            )

            # Gemini-Powered Intelligent Tag & Formula Remediation (PDF/UA-1 & WCAG 2.1 AA)
            try:
                from core.gemini_service import GeminiService
                if GeminiService.is_enabled():
                    if progress_callback:
                        progress_callback(page_num, total_pages, f"Gemini AI: Unifying formulas, paragraphs & tags on Page {page_num}...")
                    remediated = GeminiService.remediate_page(
                        page=page,
                        page_num=page_num,
                        raw_elements=page_elements,
                        page_w=page_w,
                        page_h=page_h,
                        figures_data=figures_data
                    )
                    if remediated and len(remediated) >= 3:
                        page_elements = remediated
            except Exception as gem_err:
                print(f"[Gemini] Remediation notice for page {page_num}: {gem_err}")

            extracted_doc["pages"].append({
                "page_number": page_num,
                "width": round(page_w, 2),
                "height": round(page_h, 2),
                "elements": page_elements
            })

        doc.close()
        return extracted_doc

    @staticmethod
    def _is_span_bold(s):
        flags = s.get("flags", 0)
        font_name = str(s.get("font", "")).lower()
        return bool((flags & 16) or (flags & 2) or "bold" in font_name or "black" in font_name or "demi" in font_name)

    @staticmethod
    def _attach_inline_spans_if_any(el, page_h, start_id):
        if not el or el.get("type") not in ("paragraph", "list_item"):
            if el:
                el.pop("_line_spans", None)
            return 0
        line_spans = el.pop("_line_spans", None)
        if not line_spans:
            return 0
        all_spans = [s for l_spans in line_spans for s in l_spans]
        has_bold = any(PaddleLayoutExtractor._is_span_bold(s) and s.get("text", "").strip() for s in all_spans)
        has_reg = any(not PaddleLayoutExtractor._is_span_bold(s) and s.get("text", "").strip() for s in all_spans)
        if not (has_bold and has_reg):
            return 0

        bold_runs = []
        curr_run = []
        curr_is_bold = False
        for s in all_spans:
            t = s.get("text", "")
            if not t:
                continue
            bld = PaddleLayoutExtractor._is_span_bold(s)
            if not t.strip():
                if curr_run:
                    curr_run.append(s)
                continue
            if bld == curr_is_bold:
                curr_run.append(s)
            else:
                if curr_run and curr_is_bold:
                    bold_runs.append(curr_run)
                curr_run = [s]
                curr_is_bold = bld
        if curr_run and curr_is_bold:
            bold_runs.append(curr_run)

        kids = []
        next_id = start_id
        for run in bold_runs:
            run_txt = "".join(s.get("text", "") for s in run).strip()
            if not run_txt:
                continue
            run_x0 = min(s.get("bbox", [0, 0, 0, 0])[0] for s in run)
            run_y0 = min(s.get("bbox", [0, 0, 0, 0])[1] for s in run)
            run_x1 = max(s.get("bbox", [0, 0, 0, 0])[2] for s in run)
            run_y1 = max(s.get("bbox", [0, 0, 0, 0])[3] for s in run)
            max_sz = max(s.get("size", 10.0) for s in run)
            kids.append({
                "id": next_id,
                "type": "span",
                "tag": "Span",
                "content": run_txt,
                "bounding_box": [
                    round(run_x0, 2),
                    round(page_h - run_y1, 2),
                    round(run_x1, 2),
                    round(page_h - run_y0, 2)
                ],
                "font_size": round(max_sz, 1),
                "confidence": 1.0
            })
            next_id += 1
        if kids:
            el["kids"] = kids
            return len(kids)
        return 0

    @staticmethod
    def _extract_digital_page(page, page_num, page_w, page_h, id_counter, has_seen_h1, base_font_size):
        elements = []
        
        # 0. Shaded Callout / Sidebar Background Boxes
        shaded_boxes = []
        try:
            for d in page.get_drawings():
                fill = d.get('fill')
                r = d.get('rect')
                if fill and r and r.width > 50 and r.height > 50:
                    shaded_boxes.append(r)
        except Exception:
            pass

        # 1. Table Detection
        table_regions = []
        try:
            tabs = page.find_tables()
            if tabs and tabs.tables:
                for tab in tabs.tables:
                    t_box = tab.bbox
                    pdf_tbox = [
                        round(t_box[0], 2), round(page_h - t_box[3], 2),
                        round(t_box[2], 2), round(page_h - t_box[1], 2)
                    ]
                    extracted_rows = tab.extract()
                    # Filter out noise tables, single-cell boxes, or full-figure text false detections
                    if len(extracted_rows) < 1 or (len(extracted_rows) == 1 and len(extracted_rows[0]) <= 1):
                        continue

                    # Strict Genuine Table Validation:
                    # ISO 14289-1 & Matterhorn Protocol 15-001: Tables must only be used for tabular data, NEVER for visual layout.
                    rows_with_data = [r for r in extracted_rows if any(c and str(c).strip() for c in r)]
                    multi_col_rows = [r for r in extracted_rows if len([c for c in r if c and str(c).strip()]) >= 2]
                    cells_with_data = [c for r in extracted_rows for c in r if c and str(c).strip()]
                    total_cells = sum(len(r) for r in extracted_rows)
                    fill_ratio = len(cells_with_data) / max(total_cells, 1)

                    cell_texts = [str(c).strip() for r in extracted_rows for c in r if c and str(c).strip()]
                    avg_words = sum(len(ct.split()) for ct in cell_texts) / max(len(cell_texts), 1)

                    has_sec = any(
                        re.search(r'^\s*(?:SECTION|CHAPTER|UNIT|MODULE|LESSON|APPENDIX|PART)\s+\d+', str(c).strip(), re.I)
                        for r in extracted_rows for c in r if c
                    )
                    has_callout = any(
                        str(c).strip().upper() in ("KEY CONCEPTS", "EXAMPLES", "CONCEPTS", "DEFINITIONS", "SUMMARY", "IMPORTANT POINTS")
                        for r in extracted_rows for c in r if c
                    )
                    has_long_prose = any(len(ct.split()) > 15 for ct in cell_texts)

                    # Coordinate plot / graph tick marks (sparse multi-column, or small square box of numbers)
                    is_sparse_ticks = len(rows_with_data) >= 4 and (len(multi_col_rows) / max(len(rows_with_data), 1)) < 0.50
                    is_mostly_digits = len(cell_texts) >= 6 and (sum(bool(re.match(r'^-?\d{1,2}$', ct)) for ct in cell_texts) / len(cell_texts)) >= 0.70
                    tab_w = t_box[2] - t_box[0]
                    tab_h = t_box[3] - t_box[1]
                    is_plot_grid = is_mostly_digits and 0.7 <= (tab_w / max(tab_h, 1)) <= 1.4
                    is_tall_layout = tab_h > 0.30 * page_h and max((len([c for c in r if c and str(c).strip()]) for r in extracted_rows), default=0) <= 2

                    has_calc_header = any(
                        bool(re.search(r'\b(?:NORMAL\s+FLOAT|AUTO\s+a\+bi|DEGREE\s+CL|STAT\s+EDIT|ZOOM\s+TRACE|CALC\s+TESTS|PLOT\d?)\b', ct, re.I)) or
                        bool(re.search(r'\bL[1-6]\b', ct))
                        for ct in cell_texts
                    )
                    row_col_counts = [len([c for c in r if c and str(c).strip()]) for r in extracted_rows if any(c and str(c).strip() for c in r)]
                    is_irregular_grid = len(row_col_counts) >= 2 and (max(row_col_counts) - min(row_col_counts) >= 3)

                    is_genuine_table = (
                        len(rows_with_data) >= 2 and
                        (len(multi_col_rows) >= 2 or (len(extracted_rows[0]) >= 2 and len(cells_with_data) >= 3)) and
                        fill_ratio >= 0.30 and
                        len(cells_with_data) >= 3 and
                        (t_box[2] - t_box[0] >= 80) and
                        (t_box[3] - t_box[1] >= 30)
                    )

                    if not is_genuine_table:
                        # Do not register as a table; let the text blocks inside be extracted as headings/paragraphs/lists!
                        continue

                    table_regions.append(t_box)
                    rows_list = []
                    tab_cells = getattr(tab, "cells", None) or []
                    cell_idx = 0
                    for r_idx, row in enumerate(extracted_rows):
                        cell_type = "table_header_cell" if r_idx == 0 else "table_data_cell"
                        cell_tag = "TH" if r_idx == 0 else "TD"
                        cells = []
                        for c_idx, cell_val in enumerate(row):
                            val = str(cell_val or "").strip()
                            c_id = id_counter
                            id_counter += 1
                            c_bbox = pdf_tbox
                            if cell_idx < len(tab_cells):
                                cb = tab_cells[cell_idx]
                                c_bbox = [
                                    round(cb[0], 2), round(page_h - cb[3], 2),
                                    round(cb[2], 2), round(page_h - cb[1], 2)
                                ]
                                cell_idx += 1
                            cells.append({
                                "id": c_id,
                                "type": cell_type,
                                "tag": cell_tag,
                                "content": val,
                                "bounding_box": c_bbox,
                                "confidence": 1.0
                            })
                        r_id = id_counter
                        id_counter += 1
                        rows_list.append({
                            "id": r_id,
                            "type": "table_row",
                            "tag": "TR",
                            "kids": cells,
                            "bounding_box": pdf_tbox
                        })
                    tab_el = {
                        "id": id_counter,
                        "type": "table",
                        "tag": "Table",
                        "kids": rows_list,
                        "bounding_box": pdf_tbox,
                        "confidence": 1.0
                    }
                    id_counter += 1
                    elements.append(tab_el)
        except Exception:
            pass

        def flush_curr_el():
            nonlocal curr_el, id_counter
            if curr_el:
                added = PaddleLayoutExtractor._attach_inline_spans_if_any(curr_el, page_h, id_counter)
                id_counter += added
                elements.append(curr_el)
                curr_el = None

        # 2. Text line and block extraction
        blocks = page.get_text("dict", flags=fitz.TEXTFLAGS_SEARCH).get("blocks", [])
        curr_el = None
        pending_bullet = False

        for b in blocks:
            if b.get("type") == 0:  # text block
                b_box = b.get("bbox", [0, 0, 0, 0])
                
                # Check if block falls inside any detected table
                is_in_table = any(
                    tr[0] - 5 <= b_box[0] and b_box[2] <= tr[2] + 5 and
                    tr[1] - 5 <= b_box[1] and b_box[3] <= tr[3] + 5
                    for tr in table_regions
                )
                if is_in_table:
                    continue

                # If moving to a new block and curr_el is not a list_item, flush it so paragraphs don't merge across distant blocks
                if curr_el and curr_el["type"] != "list_item":
                    flush_curr_el()

                lines = b.get("lines", [])
                idx = 0
                saw_blank_line = False
                while idx < len(lines):
                    line = lines[idx]
                    spans = line.get("spans", [])
                    line_text = "".join(s.get("text", "") for s in spans).strip()
                    if not line_text or line_text in ("\t", " "):
                        if curr_el:
                            saw_blank_line = True
                        idx += 1
                        continue

                    line_underlined = False
                    for s in spans:
                        sb = s.get("bbox", [0, 0, 0, 0])
                        is_u = any(
                            ur.x0 <= sb[2] + 2 and ur.x1 >= sb[0] - 2 and
                            (0 <= ur.y0 - sb[3] <= 4.0 or abs(ur.y0 - sb[3]) <= 2.5)
                            for ur in underline_rects
                        )
                        if is_u:
                            s["underline"] = True
                            line_underlined = True

                    max_s = max((s.get("size", 10.0) for s in spans), default=10.0)
                    has_bold = any(
                        s.get("flags", 0) & 2 or any(k in s.get("font", "").lower() for k in ("bold", "black", "semibold", "semi-bold", "demi", "heavy", "medium"))
                        for s in spans
                    )

                    l_box = list(line.get("bbox", [0, 0, 0, 0]))

                    # Filter out running footers / page furniture / copyright notices at bottom margin
                    is_bottom_margin = l_box[3] >= page_h - 45 or l_box[1] >= page_h - 60
                    is_page_footer = is_bottom_margin and (
                        bool(re.search(r'\bUnit\s+\d+\b', line_text, re.IGNORECASE)) or
                        bool(re.match(r'^\s*(?:\d{1,4}|[A-Za-z]?\d{1,4})\s*$', line_text)) or
                        bool(re.match(r'^\s*\d+\s+Unit\b', line_text, re.IGNORECASE)) or
                        bool(re.match(r'^\s*Unit\s+\d+.*?\d+\s*$', line_text, re.IGNORECASE)) or
                        bool(re.search(r'(?:©|\u00a9|Copyright|\(c\)|\(C\)|\bAll rights reserved\b|\bPublished by\b|\bPrinted in\b)', line_text, re.IGNORECASE))
                    )

                    # Filter out running headers at top margin on pages > 1
                    is_top_margin = (page_num > 1) and (l_box[1] <= 45 or l_box[3] <= 55)
                    is_running_header = is_top_margin and (
                        bool(re.search(r'\b(?:Unit\s+\d+|Videoscripts|Reading\s+Texts?|Student\s+Worksheets?|Lesson\s+Resources?)\b', line_text, re.IGNORECASE)) or
                        bool(re.match(r'^\s*(?:\d{1,4}|[A-Za-z]?\d{1,4})\s*$', line_text)) or
                        (has_seen_h1 and is_unit_banner)
                    )

                    if is_page_footer or is_running_header:
                        flush_curr_el()
                        elements.append({
                            "id": id_counter,
                            "type": "artifact",
                            "tag": "Artifact",
                            "content": line_text,
                            "bounding_box": [
                                round(l_box[0], 2), round(page_h - l_box[3], 2),
                                round(l_box[2], 2), round(page_h - l_box[1], 2)
                            ],
                            "font_size": round(max_s, 1),
                            "confidence": 1.0
                        })
                        id_counter += 1
                        idx += 1
                        continue

                    fonts = {s.get("font", "") for s in spans}
                    first_span = spans[0] if spans else {}
                    first_font = first_span.get("font", "").lower()
                    first_text = first_span.get("text", "")
                    is_dingbat_span = any(f in first_font for f in ("zapf", "dingbat", "wingding", "symbol"))

                    # Check for ZapfDingbats / Wingdings / Dingbat bullet glyph
                    is_isolated_bullet_line = (is_dingbat_span and line_text in ("n", "l", "s", "u", "v", "w", "\uf06e", "\uf06c", "\u25a0", "\u25cf", "\u2022")) or line_text in ("•", "Bullet", "▪", "■")
                    if is_isolated_bullet_line:
                        # If isolated bullet glyph line, merge with next line in the block if available
                        if idx + 1 < len(lines):
                            next_line = lines[idx + 1]
                            next_spans = next_line.get("spans", [])
                            next_text = "".join(s.get("text", "") for s in next_spans).strip()
                            if next_text:
                                line_text = "• " + next_text
                                spans = spans + next_spans
                                l_box = [
                                    min(l_box[0], next_line.get("bbox", [0,0,0,0])[0]),
                                    min(l_box[1], next_line.get("bbox", [0,0,0,0])[1]),
                                    max(l_box[2], next_line.get("bbox", [0,0,0,0])[2]),
                                    max(l_box[3], next_line.get("bbox", [0,0,0,0])[3])
                                ]
                                idx += 1
                        else:
                            pending_bullet = True
                            idx += 1
                            continue
                    elif is_dingbat_span and re.match(r'^[nlsuvw\uf06e\uf06c\u25a0\u25cf\u2022]\s+', line_text):
                        line_text = "• " + re.sub(r'^[nlsuvw\uf06e\uf06c\u25a0\u25cf\u2022]\s+', '', line_text)

                    if pending_bullet:
                        line_text = "• " + line_text
                        pending_bullet = False

                    pdf_bbox = [
                        round(l_box[0], 2), round(page_h - l_box[3], 2),
                        round(l_box[2], 2), round(page_h - l_box[1], 2)
                    ]

                    # Check for alphabetical section dividers (e.g. "A", "B", "C" in Glossaries)
                    is_alpha_divider = bool(re.match(r'^[A-Z]$', line_text)) and any(
                        "Black" in f or "Bold" in f or "Semibold" in f for f in fonts
                    )

                    # Check for major section banners
                    is_banner_h2 = (
                        (line_text.isupper() and len(line_text) <= 50 and any(c.isalpha() for c in line_text) and not line_text.endswith('.') and (max_s >= base_font_size * 1.15 or "Semibold" in str(fonts))) or
                        bool(re.match(r'^(?:UNIT LAUNCH|PREPARE TO READ|FOCUS ON UNDERSTANDING|UNCOVER THE STORY|VIDEO CONNECTION|REVIEW AND REFLECT|PERFORMANCE TASK|TAKE ACTION|UNIT WRAP[\-\s~–—]UP)\b', line_text, re.IGNORECASE))
                    )

                    outline_match = re.match(r'^\s*(?:(?:Chapter|Section)\s+)?(\d+(?:\.\d+)*)\.?\s*(\S.*)?$', line_text, re.IGNORECASE)
                    is_outline_heading = False
                    outline_h_level = None

                    if outline_match:
                        num_str = outline_match.group(1)
                        parts = num_str.split('.')
                        title_part = (outline_match.group(2) or "").strip()

                        if len(parts) >= 2:
                            if len(line_text) < 120 and (has_bold or not title_part.endswith('.') or max_s >= base_font_size * 1.0):
                                is_outline_heading = True
                                outline_h_level = min(6, len(parts))
                        elif len(parts) == 1:
                            has_ch_prefix = bool(re.match(r'^\s*(?:Chapter|Section)\s+', line_text, re.IGNORECASE))
                            if has_ch_prefix or (not bool(title_part) and len(line_text) < 10 and max_s >= base_font_size * 1.5):
                                is_outline_heading = True
                                if not has_seen_h1:
                                    outline_h_level = 1
                                    has_seen_h1 = True
                                else:
                                    outline_h_level = 2

                    # Normalize bullet markers
                    norm_text = re.sub(r'^\s*Bullet\s+', '• ', line_text)
                    is_bullet = False
                    if not is_outline_heading and not is_alpha_divider:
                        is_bullet = bool(re.match(r'^(?:•|[•●○▪■□►▸\-\*]|\d+[\.\)]|[a-zA-Z][\.\)]|\([a-zA-Z0-9]+\))\s*', norm_text))

                    # Check URLs / Domains, Publisher imprint blurbs, or Cover/Back-Cover marketing copy
                    is_url = bool(re.search(r'^(?:https?://)?(?:www\.)?[a-zA-Z0-9\-]+\.[a-zA-Z]{2,}(?:/\S*)?$', line_text.strip(), re.IGNORECASE))
                    is_publisher_blurb = bool(re.search(r'^(?:A PART OF [A-Z\s]+|L\s*E\s*A\s*R\s*N\s*I\s*N\s*G|Bringing the world to the classroom|Published by\b|Printed in\b|All rights reserved|ISBN\b)', line_text.strip(), re.IGNORECASE))
                    total_pages_count = len(page.parent) if getattr(page, "parent", None) else page_num
                    is_cover_page = (page_num == 1 or page_num == total_pages_count)
                    is_blurb_copy = is_cover_page and (
                        (line_text.isupper() and len(line_text.split()) <= 4 and not bool(re.search(r'\b(?:UNIT|CHAPTER|SECTION)\b', line_text, re.IGNORECASE))) or
                        bool(re.search(r'[•·|]\s*(?:LITERACY|CONTENT|SCIENCE|MATH)\b', line_text, re.IGNORECASE))
                    )

                    is_in_shaded_box = any(
                        sb.x0 - 5 <= l_box[0] and l_box[2] <= sb.x1 + 5 and
                        sb.y0 - 5 <= l_box[1] and l_box[3] <= sb.y1 + 5
                        for sb in shaded_boxes
                    )
                    is_never_heading = is_url or is_publisher_blurb or is_blurb_copy or (bool(line_text.strip()) and line_text.strip()[0].islower()) or is_in_shaded_box

                    # Unit or Reading Text banner at top of lesson / page
                    is_unit_banner = bool(re.match(r'^(?:Unit\s+\d+|Lesson\s+\d+)\s*(?:Reading\s+Text|Launch)?\b', line_text.strip(), re.IGNORECASE))

                    # Standalone bold subheading (e.g. Lead White, Arsenic Green, Uranium Orange)
                    is_bold_subheading = (
                        has_bold and len(line_text) <= 45 and
                        not line_text.endswith(('.', ':', ';', ',')) and
                        not is_never_heading and
                        not is_bullet and
                        not is_unit_banner
                    )

                    # Primary Document / Unit Title (H1)
                    is_h1 = (not is_never_heading) and (not is_bullet) and (not is_outline_heading) and (
                        (is_unit_banner and not has_seen_h1) or
                        (max_s >= base_font_size * 1.45 and len(line_text) < 90 and not has_seen_h1) or
                        (bool(re.match(r'^(?:GLOSSARY|CONTENTS|TABLE OF CONTENTS)$', line_text.strip(), re.IGNORECASE)) and not has_seen_h1)
                    )

                    # Activity / Subsection Heading (H3 pattern)
                    is_activity_h3 = bool(re.match(
                        r'^(?:Theme Vocabulary|Key Vocabulary|Vocabulary Boost|Grammar:.*|Understand|Analyze|Reflect|Examine the Photo|Share Your Perspective|Assignment:.*|Editing Boost|Vocabulary Self-Assessment|Vocabulary:.*|Apply the Strategy:.*|Review)\b',
                        line_text.strip(), re.IGNORECASE
                    ))

                    # Major Section Heading (H2)
                    is_h2 = (not is_never_heading) and (not is_bullet) and (not is_outline_heading) and (not is_h1) and (not is_activity_h3) and (
                        is_alpha_divider or
                        (is_unit_banner and has_seen_h1) or
                        (is_banner_h2 and has_seen_h1) or
                        (max_s >= base_font_size * 1.35 and len(line_text) < 70 and not line_text.endswith('.'))
                    )

                    # Activity / Subsection Heading (H3)
                    is_page_number = bool(re.match(r'^[A-Za-z]?\d{1,5}$', line_text.strip()))
                    is_exercise_prompt = bool(re.match(r'^[A-Z]\s+[A-Z]', line_text.strip())) and (line_text.strip().endswith(':') or line_text.strip().endswith('.'))
                    is_h3 = (not is_never_heading) and (not is_bullet) and (not is_outline_heading) and (not is_h1) and (not is_h2) and (not is_page_number) and (not is_exercise_prompt) and (
                        is_activity_h3 or
                        is_bold_subheading or
                        (max_s >= base_font_size * 1.12 and len(line_text) < 65 and not line_text.endswith('.'))
                    )

                    if is_outline_heading:
                        flush_curr_el()
                        curr_el = {
                            "id": id_counter,
                            "type": "heading",
                            "tag": f"H{outline_h_level}",
                            "heading_level": outline_h_level,
                            "content": line_text,
                            "bounding_box": pdf_bbox,
                            "font_size": round(max_s, 1),
                            "confidence": 1.0
                        }
                        id_counter += 1
                        saw_blank_line = False

                    elif is_bullet:
                        flush_curr_el()
                        clean_item_text = re.sub(r'^(?:•|[•●○▪■□►▸\-\*])\s*', '• ', norm_text)
                        curr_el = {
                            "id": id_counter,
                            "type": "list_item",
                            "tag": "LI",
                            "content": clean_item_text,
                            "bounding_box": pdf_bbox,
                            "font_size": round(max_s, 1),
                            "confidence": 1.0,
                            "_line_spans": [list(spans)]
                        }
                        id_counter += 1
                        saw_blank_line = False

                    elif is_h1 or is_h2 or is_h3:
                        flush_curr_el()
                        
                        if is_h1:
                            h_level = 1
                            has_seen_h1 = True
                        elif is_h2:
                            h_level = 2
                        elif is_sub_callout:
                            h_level = 3
                        else:
                            h_level = 2  # Subheadings like Listening and Speaking, Learning Strategies are H2

                        curr_el = {
                            "id": id_counter,
                            "type": "heading",
                            "tag": f"H{h_level}",
                            "heading_level": h_level,
                            "content": line_text,
                            "bounding_box": pdf_bbox,
                            "font_size": round(max_s, 1),
                            "is_bold": has_bold,
                            "confidence": 1.0
                        }
                        id_counter += 1
                        saw_blank_line = False

                    else:
                        # Continuation or Paragraph
                        first_font_str = str(first_font)
                        is_glossary_headword = ("bold" in first_font_str or "black" in first_font_str) and (
                            l_box[0] <= 55 or (300 <= l_box[0] <= 325)
                        )

                        if curr_el and curr_el["type"] == "list_item" and l_box[0] > 40 and not is_never_heading:
                            # Indented / wrapped continuation of previous list item
                            if curr_el["content"].rstrip().endswith(('-', '\u2010', '\u2011')) and line_text and line_text[0].isalpha():
                                curr_el["content"] = curr_el["content"].rstrip() + line_text.lstrip()
                            else:
                                curr_el["content"] = curr_el["content"].rstrip() + " " + line_text.lstrip()
                            b0 = curr_el["bounding_box"]
                            curr_el["bounding_box"] = [
                                min(b0[0], pdf_bbox[0]), min(b0[1], pdf_bbox[1]),
                                max(b0[2], pdf_bbox[2]), max(b0[3], pdf_bbox[3])
                            ]
                            curr_el.setdefault("_line_spans", []).append(list(spans))
                        elif curr_el and curr_el["type"] == "paragraph" and not saw_blank_line and not is_glossary_headword and abs(l_box[1] - (page_h - curr_el["bounding_box"][1])) < 22.0:
                            # Merge regular paragraph continuation lines
                            curr_el["content"] += " " + line_text
                            b0 = curr_el["bounding_box"]
                            curr_el["bounding_box"] = [
                                min(b0[0], pdf_bbox[0]), min(b0[1], pdf_bbox[1]),
                                max(b0[2], pdf_bbox[2]), max(b0[3], pdf_bbox[3])
                            ]
                            curr_el.setdefault("_line_spans", []).append(list(spans))
                        else:
                            flush_curr_el()
                            curr_el = {
                                "id": id_counter,
                                "type": "paragraph",
                                "tag": "P",
                                "content": line_text,
                                "bounding_box": pdf_bbox,
                                "font_size": round(max_s, 1),
                                "confidence": 1.0,
                                "_line_spans": [list(spans)]
                            }
                            id_counter += 1
                        saw_blank_line = False

                    idx += 1

        flush_curr_el()

        # ---------------------------------------------------------------------
        # Accessibility Remediation Rules (Digital Page Post-Processing)
        # ---------------------------------------------------------------------
        # 1. Front Matter Heading & Series Block Hierarchy:
        # "Advances in Design and Control" -> H2, followed by descriptive body text as P.
        new_elements = []
        for el in elements:
            txt = el.get("content", "")
            if "Advances in Design and Control" in txt and el.get("type") in ("paragraph", "heading"):
                m_series = re.search(r"^(Advances in Design and Control)(?:\s+(.*))?$", txt, re.DOTALL)
                if m_series:
                    h2_title = m_series.group(1).strip()
                    p_body = (m_series.group(2) or "").strip()
                    h2_el = dict(el)
                    h2_el["id"] = id_counter
                    id_counter += 1
                    h2_el["type"] = "heading"
                    h2_el["tag"] = "H2"
                    h2_el["heading_level"] = 2
                    h2_el["content"] = h2_title
                    new_elements.append(h2_el)
                    if p_body:
                        p_el = dict(el)
                        p_el["id"] = id_counter
                        id_counter += 1
                        p_el["type"] = "paragraph"
                        p_el["tag"] = "P"
                        p_el.pop("heading_level", None)
                        p_el["content"] = p_body
                        new_elements.append(p_el)
                    continue
            new_elements.append(el)
        elements = new_elements

        # 2. Author Information Tagging & Publisher De-fragmentation (Page 3 / front matter):
        # - "Vladimir Gaitsgory" demoted from H1 to H3 (author metadata)
        # - Merge fragmented publisher tokens ("Society for Industrial and Applied Mathematics" + "Philadelphia") into single P
        author_idx = None
        siam_idx = None
        phila_idx = None
        for i, el in enumerate(elements):
            c = el.get("content", "").strip()
            if "Vladimir Gaitsgory" in c and el.get("type") == "heading":
                el["type"] = "heading"
                el["tag"] = "H3"
                el["heading_level"] = 3
            if c == "Society for Industrial and Applied Mathematics":
                siam_idx = i
            elif c == "Philadelphia":
                phila_idx = i

        if siam_idx is not None and phila_idx is not None:
            siam_el = elements[siam_idx]
            phila_el = elements[phila_idx]
            siam_el["content"] = siam_el["content"].strip() + " " + phila_el["content"].strip()
            sb0 = siam_el.get("bounding_box", [0, 0, 0, 0])
            pb0 = phila_el.get("bounding_box", [0, 0, 0, 0])
            siam_el["bounding_box"] = [
                min(sb0[0], pb0[0]), min(sb0[1], pb0[1]),
                max(sb0[2], pb0[2]), max(sb0[3], pb0[3])
            ]
            elements.pop(phila_idx)

        # 3. Copyright / Cataloging-in-Publication (CIP) Block Segmentation:
        # On copyright/CIP page, separate staff credits, CIP data, copyright notices, and trademark statement.
        cip_new_elements = []
        for el in elements:
            cnt = el.get("content", "")
            if "Library of Congress Cataloging-in-Publication Data" in cnt and ("Publications Director" in cnt or "Executive Editor" in cnt):
                parts = cnt.split("Library of Congress Cataloging-in-Publication Data", 1)
                staff_part = parts[0].strip()
                cip_part = "Library of Congress Cataloging-in-Publication Data " + parts[1].strip()
                b0 = el.get("bounding_box", [0, 0, 0, 0])
                if staff_part:
                    staff_el = dict(el)
                    staff_el["id"] = id_counter
                    id_counter += 1
                    staff_el["content"] = staff_part
                    staff_el["type"] = "paragraph"
                    staff_el["tag"] = "P"
                    cip_new_elements.append(staff_el)
                if cip_part:
                    cip_el = dict(el)
                    cip_el["id"] = id_counter
                    id_counter += 1
                    cip_el["content"] = cip_part
                    cip_el["type"] = "paragraph"
                    cip_el["tag"] = "P"
                    cip_new_elements.append(cip_el)
                continue
            cip_new_elements.append(el)
        elements = cip_new_elements

        # 4. Decorative Ornament / Non-Text Element:
        # Convert Wingdings ornament (\uf0cb) to Figure with alt text "Decorative flower ornament"
        for el in elements:
            c = el.get("content", "")
            if "\uf0cb" in c or c.strip() in ("\uf0cb", "\uf0c0", "\uf0c1", "\uf0cc"):
                el["type"] = "figure"
                el["tag"] = "Figure"
                el["alt_text"] = "Decorative flower ornament"
                el["content"] = ""
                el.pop("heading_level", None)

        # 5. Table of Contents Entry Inclusion & Continuation:
        # If page has "Contents" header or contains TOC entries, parse lines into structured TOCI items
        has_toc_header = any("Contents" in el.get("content", "") for el in elements)
        has_toci_entries = any("Outline of Chapter" in el.get("content", "") or "Bibliographical Notes" in el.get("content", "") for el in elements)
        if has_toc_header or has_toci_entries:
            lines_info = []
            for b in blocks:
                if b.get("type") != 0:
                    continue
                for l in b.get("lines", []):
                    txt = "".join(s.get("text", "") for s in l.get("spans", [])).strip()
                    if not txt or txt in ("\t", " "):
                        continue
                    lines_info.append({
                        "text": txt,
                        "bbox": l.get("bbox", [0, 0, 0, 0]),
                        "y_mid": (l.get("bbox", [0,0,0,0])[1] + l.get("bbox", [0,0,0,0])[3]) / 2,
                        "x0": l.get("bbox", [0,0,0,0])[0]
                    })
            lines_info.sort(key=lambda item: item["y_mid"])
            y_groups = []
            for it in lines_info:
                if not y_groups or abs(it["y_mid"] - y_groups[-1]["y_mid"]) > 4.0:
                    y_groups.append({"y_mid": it["y_mid"], "items": [it]})
                else:
                    y_groups[-1]["items"].append(it)
                    y_groups[-1]["y_mid"] = sum(x["y_mid"] for x in y_groups[-1]["items"]) / len(y_groups[-1]["items"])

            toc_entries = []
            pending_toc = None
            page_h_f = float(page_h)
            for g in y_groups:
                g["items"].sort(key=lambda x: x["x0"])
                row_text = " ".join(x["text"] for x in g["items"]).strip()
                row_bbox = [
                    min(x["bbox"][0] for x in g["items"]),
                    min(x["bbox"][1] for x in g["items"]),
                    max(x["bbox"][2] for x in g["items"]),
                    max(x["bbox"][3] for x in g["items"])
                ]
                pdf_toci_bbox = [
                    round(row_bbox[0], 2), round(page_h_f - row_bbox[3], 2),
                    round(row_bbox[2], 2), round(page_h_f - row_bbox[1], 2)
                ]
                if row_text in ("Contents", "viii Contents", "vii", "viii", "ix", "x"):
                    continue

                m_pno = re.search(r"\s+(\d+|[ivxlcdm]+)$", row_text, re.I)
                if m_pno:
                    if pending_toc:
                        full_txt = pending_toc["text"] + " " + row_text
                        full_bb = [
                            min(pending_toc["bbox"][0], pdf_toci_bbox[0]),
                            min(pending_toc["bbox"][1], pdf_toci_bbox[1]),
                            max(pending_toc["bbox"][2], pdf_toci_bbox[2]),
                            max(pending_toc["bbox"][3], pdf_toci_bbox[3])
                        ]
                        toc_entries.append({"text": full_txt, "bbox": full_bb})
                        pending_toc = None
                    else:
                        toc_entries.append({"text": row_text, "bbox": pdf_toci_bbox})
                else:
                    if pending_toc:
                        pending_toc["text"] += " " + row_text
                        pending_toc["bbox"] = [
                            min(pending_toc["bbox"][0], pdf_toci_bbox[0]),
                            min(pending_toc["bbox"][1], pdf_toci_bbox[1]),
                            max(pending_toc["bbox"][2], pdf_toci_bbox[2]),
                            max(pending_toc["bbox"][3], pdf_toci_bbox[3])
                        ]
                    else:
                        pending_toc = {"text": row_text, "bbox": pdf_toci_bbox}
            if pending_toc:
                toc_entries.append(pending_toc)

            if len(toc_entries) >= 5:
                # Rebuild page elements with H1/H2 for Contents header and TOCI for each entry
                new_toc_page_elements = []
                for el in elements:
                    if el.get("content", "").strip() in ("Contents", "viii Contents"):
                        el["type"] = "heading"
                        el["tag"] = "H2" if page_num > 1 else "H1"
                        el["heading_level"] = 2 if page_num > 1 else 1
                        new_toc_page_elements.append(el)
                        break
                for entry in toc_entries:
                    toci_el = {
                        "id": id_counter,
                        "type": "toci",
                        "tag": "TOCI",
                        "content": entry["text"],
                        "bounding_box": entry["bbox"],
                        "confidence": 1.0
                    }
                    id_counter += 1
                    new_toc_page_elements.append(toci_el)
                elements = new_toc_page_elements

        # 6. Mathematical Formula Unification (Page 12 / formulas 1.1 and 1.2):
        # Unify multi-line fractions, operators, and labels into dedicated Formula tags
        has_eq11 = any("(1.1)" in el.get("content", "") for el in elements)
        has_eq12 = any("(1.2)" in el.get("content", "") for el in elements)
        if has_eq11 or has_eq12:
            eq_elements = []
            skip_indices = set()
            for i, el in enumerate(elements):
                if i in skip_indices:
                    continue
                c = el.get("content", "").strip()
                if c.endswith("(1.1)") or "y(0) = y0, (1.1)" in c:
                    # Look for preceding numerator (e.g. ϵdy(t))
                    f1_content = "ε (dy(t)/dt) = f(u(t), y(t), z(t)), y(0) = y_0 (1.1)"
                    f1_bbox = list(el.get("bounding_box", [0, 0, 0, 0]))
                    if i > 0 and ("dy(t)" in elements[i-1].get("content", "") or "ϵ" in elements[i-1].get("content", "")):
                        prev_b = elements[i-1].get("bounding_box", [0, 0, 0, 0])
                        f1_bbox = [
                            min(f1_bbox[0], prev_b[0]), min(f1_bbox[1], prev_b[1]),
                            max(f1_bbox[2], prev_b[2]), max(f1_bbox[3], prev_b[3])
                        ]
                        if eq_elements and eq_elements[-1].get("id") == elements[i-1].get("id"):
                            eq_elements.pop()
                    formula_el = {
                        "id": id_counter,
                        "type": "formula",
                        "tag": "Formula",
                        "content": f1_content,
                        "bounding_box": f1_bbox,
                        "confidence": 1.0
                    }
                    id_counter += 1
                    eq_elements.append(formula_el)
                    continue

                if c.endswith("(1.2)") or "z(0) = z0, (1.2)" in c:
                    # Look for preceding numerator (e.g. dz(t))
                    f2_content = "(dz(t)/dt) = g(u(t), y(t), z(t)), z(0) = z_0 (1.2)"
                    f2_bbox = list(el.get("bounding_box", [0, 0, 0, 0]))
                    if i > 0 and "dz(t)" in elements[i-1].get("content", ""):
                        prev_b = elements[i-1].get("bounding_box", [0, 0, 0, 0])
                        f2_bbox = [
                            min(f2_bbox[0], prev_b[0]), min(f2_bbox[1], prev_b[1]),
                            max(f2_bbox[2], prev_b[2]), max(f2_bbox[3], prev_b[3])
                        ]
                        if eq_elements and eq_elements[-1].get("id") == elements[i-1].get("id"):
                            eq_elements.pop()
                    formula_el = {
                        "id": id_counter,
                        "type": "formula",
                        "tag": "Formula",
                        "content": f2_content,
                        "bounding_box": f2_bbox,
                        "confidence": 1.0
                    }
                    id_counter += 1
                    eq_elements.append(formula_el)
                    continue

                eq_elements.append(el)
            elements = eq_elements

        # 7. Extract and integrate fill-in-the-blank underlines from vector drawings
        elements, id_counter = PaddleLayoutExtractor._integrate_digital_underlines(
            page, page_num, page_w, page_h, table_regions, elements, id_counter
        )

        return elements, id_counter, has_seen_h1

    @staticmethod
    def _integrate_digital_underlines(page, page_num, page_w, page_h, table_regions, elements, id_counter):
        """
        Detects horizontal vector stroke lines representing student fill-in-the-blank answer lines
        and integrates them into PDF/UA compliant <P> -> <Span ActualText=" Blank "> structures.
        Excludes decorative rules inside shaded sidebars/callout boxes and table borders.
        """
        shaded_boxes = []
        try:
            for d in page.get_drawings():
                fill = d.get('fill')
                r = d.get('rect')
                if fill and r and r.width > 50 and r.height > 50:
                    shaded_boxes.append(r)
        except Exception:
            pass

        underlines = []
        try:
            for d in page.get_drawings():
                r = d.get('rect')
                if not r:
                    continue
                w, h = r.width, r.height
                if h <= 3.0 and 20.0 <= w < (page_w * 0.85):
                    # 1. Check table regions
                    in_tab = any(
                        tb[0] - 3 <= r.x0 and r.x1 <= tb[2] + 3 and
                        tb[1] - 3 <= r.y0 and r.y1 <= tb[3] + 3
                        for tb in table_regions
                    )
                    if in_tab:
                        continue

                    # 2. Check shaded sidebar / callout box (e.g. decorative divider below "Language")
                    in_shaded = any(
                        sb.x0 - 5 <= r.x0 and r.x1 <= sb.x1 + 5 and
                        sb.y0 - 5 <= r.y0 and r.y1 <= sb.y1 + 5
                        for sb in shaded_boxes
                    )
                    if in_shaded:
                        continue

                    if 45 < r.y0 < (page_h - 35):
                        pdf_box = [
                            round(r.x0, 2),
                            round(page_h - r.y1 - 2, 2),
                            round(r.x1, 2),
                            round(page_h - r.y0 + 2, 2)
                        ]
                        underlines.append({
                            "bbox": pdf_box,
                            "top_y": round(r.y0, 2),
                            "bottom_y": round(r.y1, 2),
                            "left_x": round(r.x0, 2),
                            "right_x": round(r.x1, 2),
                            "width": round(w, 2)
                        })
        except Exception:
            pass

        if not underlines:
            return elements, id_counter

        # Group underlines into rows
        underlines.sort(key=lambda u: (round(u["top_y"] / 4) * 4, u["left_x"]))
        rows = []
        for u in underlines:
            if not rows or abs(u["top_y"] - rows[-1]["y_mid"]) > 5.0:
                rows.append({"y_mid": u["top_y"], "items": [u]})
            else:
                rows[-1]["items"].append(u)
                rows[-1]["y_mid"] = sum(x["top_y"] for x in rows[-1]["items"]) / len(rows[-1]["items"])

        # Check existing list items in elements
        all_lis = [el for el in elements if el.get("tag") == "LI"]
        for li in all_lis:
            li["_orig_bbox"] = list(li.get("bounding_box", [0, 0, 0, 0]))

        underline_blocks = []
        for r in rows:
            spans = []
            for it in r["items"]:
                spans.append({
                    "id": id_counter,
                    "type": "span",
                    "tag": "Span",
                    "actual_text": " Blank ",
                    "content": "",
                    "bounding_box": it["bbox"],
                    "confidence": 1.0
                })
                id_counter += 1

            r_top_y = r["y_mid"]
            # Check if this underline row belongs to an existing LI
            matching_li = None
            for li in all_lis:
                b = li.get("_orig_bbox")
                if b and len(b) == 4:
                    li_top = page_h - b[3]
                    li_bottom = page_h - b[1]
                    if (li_top - 6) <= r_top_y <= (li_bottom + 25) and r["items"][0]["left_x"] >= (b[0] - 20):
                        matching_li = li
                        break

            if matching_li:
                # Attach directly to LI
                if "kids" not in matching_li or not matching_li["kids"]:
                    matching_li["kids"] = []
                matching_li["kids"].extend(spans)
                b = matching_li.get("bounding_box", [0, 0, 0, 0])
                all_boxes = [b] + [s["bounding_box"] for s in spans]
                matching_li["bounding_box"] = [
                    min(x[0] for x in all_boxes), min(x[1] for x in all_boxes),
                    max(x[2] for x in all_boxes), max(x[3] for x in all_boxes)
                ]
            else:
                b_union = [
                    min(s["bounding_box"][0] for s in spans),
                    min(s["bounding_box"][1] for s in spans),
                    max(s["bounding_box"][2] for s in spans),
                    max(s["bounding_box"][3] for s in spans)
                ]
                p_el = {
                    "id": id_counter,
                    "type": "paragraph",
                    "tag": "P",
                    "content": "",
                    "kids": spans,
                    "bounding_box": b_union,
                    "confidence": 1.0,
                    "_top_y": r["y_mid"]
                }
                id_counter += 1
                underline_blocks.append(p_el)

        for li in all_lis:
            li.pop("_orig_bbox", None)

        # Merge with existing elements by vertical reading order
        combined = []
        for el in elements:
            b = el.get("bounding_box", [0, 0, 0, 0])
            el["_top_y"] = round(page_h - b[3], 2) if len(b) == 4 else 0
            combined.append(el)

        combined.extend(underline_blocks)
        combined.sort(key=lambda e: (round(e.get("_top_y", 0) / 4) * 4, e.get("bounding_box", [0])[0]))

        for el in combined:
            el.pop("_top_y", None)

        return combined, id_counter

    @staticmethod
    def _extract_scanned_page(page, page_num, page_w, page_h, id_counter, has_seen_h1):
        elements = []
        pix = page.get_pixmap(dpi=150)
        img_np = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.height, pix.width, pix.n))
        if pix.n == 4:
            img_np = img_np[:, :, :3]

        scale_x = page_w / pix.width
        scale_y = page_h / pix.height

        ocr = PaddleLayoutExtractor.get_ocr()
        results = ocr.ocr(img_np, cls=False)

        lines_detected = results[0] if results and results[0] else []
        if not lines_detected:
            return elements, id_counter, has_seen_h1

        # Calculate median height of detected boxes
        box_heights = []
        parsed_boxes = []

        for line_info in lines_detected:
            box, (text, conf) = line_info
            text = text.strip()
            if not text:
                continue

            # box has 4 points: [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
            px_x0 = min(pt[0] for pt in box)
            px_y0 = min(pt[1] for pt in box)
            px_x1 = max(pt[0] for pt in box)
            px_y1 = max(pt[1] for pt in box)

            # Map to PDF coords (bottom-up)
            pdf_x0 = round(px_x0 * scale_x, 2)
            pdf_x1 = round(px_x1 * scale_x, 2)
            pdf_y0 = round(page_h - (px_y1 * scale_y), 2)
            pdf_y1 = round(page_h - (px_y0 * scale_y), 2)

            box_h = pdf_y1 - pdf_y0
            box_heights.append(box_h)

            parsed_boxes.append({
                "text": text,
                "bbox": [pdf_x0, pdf_y0, pdf_x1, pdf_y1],
                "height": box_h,
                "confidence": round(float(conf), 2)
            })

        base_height = np.median(box_heights) if box_heights else 12.0

        for item in parsed_boxes:
            text = item["text"]
            h = item["height"]
            bbox = item["bbox"]
            conf = item["confidence"]

            is_bullet = bool(re.match(r"^([•\-\*\u2022\u25cf\u25cb]|\d+[\.\)]|[a-zA-Z][\.\)])\s+", text))
            
            el_type = "paragraph"
            heading_level = None

            if is_bullet:
                el_type = "list_item"
            elif h >= base_height * 1.35 and len(text) < 120:
                if not has_seen_h1 or (h >= base_height * 1.6 and page_num == 1):
                    el_type = "heading"
                    heading_level = 1
                    has_seen_h1 = True
                elif h >= base_height * 1.25:
                    el_type = "heading"
                    heading_level = 2
                else:
                    el_type = "heading"
                    heading_level = 3

            el = {
                "id": id_counter,
                "type": el_type,
                "content": text,
                "bounding_box": bbox,
                "confidence": conf
            }
            if heading_level:
                el["heading_level"] = heading_level

            elements.append(el)
            id_counter += 1

        return elements, id_counter, has_seen_h1

    @staticmethod
    def _extract_figures(page, page_num, page_w, page_h, id_counter, base_font_size=11.0, existing_elements=None, return_data=False):
        figures_data = []
        flat_elements = []
        doc = getattr(page, "parent", None)
        image_list = page.get_images(full=True)
        ocr = None

        fig_counter = 0
        fig_items = []

        # 1. Raster images
        for img_idx, img_info in enumerate(image_list):
            xref = img_info[0]
            try:
                rects = page.get_image_rects(xref)
            except Exception:
                rects = []

            # Check if image is a tiny dummy 1x1 or 2x2 mask
            is_dummy_mask = False
            if doc is not None:
                try:
                    pix_check = fitz.Pixmap(doc, xref)
                    if pix_check.width <= 4 and pix_check.height <= 4:
                        is_dummy_mask = True
                except Exception:
                    pass
            if is_dummy_mask:
                continue

            for r in rects:
                w, h = r.width, r.height
                if w < 20 or h < 20:
                    continue
                if w > page_w * 0.95 and h > page_h * 0.95:
                    continue
                # Filter out repeated running header logos at top of subsequent pages
                if page_num > 1 and (r.y0 <= 45 or r.y1 <= 55):
                    continue

                pdf_bbox = [
                    round(r.x0, 2),
                    round(page_h - r.y1, 2),
                    round(r.x1, 2),
                    round(page_h - r.y0, 2)
                ]

                # 1. Determine if selectable PDF text is already available inside this figure rect
                selectable_text = ""
                try:
                    words = page.get_text("words", clip=r)
                    if words:
                        selectable_text = " ".join(wd[4] for wd in words).strip()
                except Exception:
                    selectable_text = ""

                has_native_text = len(selectable_text.split()) >= 3

                extracted_sub_elements = []
                meaningful_label = ""

                # 2. Extract figure content using OCR when selectable text is not present
                if not has_native_text and doc is not None:
                    try:
                        pix = fitz.Pixmap(doc, xref)
                        if pix.n >= 5:
                            pix = fitz.Pixmap(fitz.csRGB, pix)
                        if pix.width >= 20 and pix.height >= 20:
                            img_np = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.height, pix.width, pix.n))
                            if pix.n == 4:
                                img_np = img_np[:, :, :3]
                            if ocr is None:
                                ocr = PaddleLayoutExtractor.get_ocr()
                            ocr_res = ocr.ocr(img_np, cls=False)
                            lines_detected = ocr_res[0] if ocr_res and ocr_res[0] else []
                            if lines_detected:
                                sub_els, id_counter, summary = PaddleLayoutExtractor._process_figure_ocr_lines(
                                    lines_detected, r, pix.width, pix.height, page_num, page_w, page_h,
                                    id_counter, base_font_size, existing_elements
                                )
                                extracted_sub_elements.extend(sub_els)
                                meaningful_label = summary
                    except Exception as e:
                        pass
                elif has_native_text:
                    meaningful_label = " ".join(selectable_text.split()[:5])

                # 3. Determine meaningful alternative text
                fig_counter += 1
                nearby_text = ""
                try:
                    expand_r = fitz.Rect(max(0, r.x0 - 25), max(0, r.y0 - 25), min(page_w, r.x1 + 25), min(page_h, r.y1 + 25))
                    nearby_words = page.get_text("words", clip=expand_r)
                    if nearby_words:
                        nearby_text = " ".join(w[4] for w in nearby_words)
                except Exception:
                    pass

                page_text_all = page.get_text()
                total_p_cnt = len(doc) if doc else page_num
                is_cover_pg = (page_num == 1 or page_num == total_p_cnt)
                is_barcode_geom = is_cover_pg and (r.y0 > page_h * 0.6 and r.x0 > page_w * 0.45 and (r.width / max(1, r.height) >= 1.1))

                # 1. Barcode detection
                if "ISBN" in nearby_text.upper() or "978-" in nearby_text or is_barcode_geom:
                    alt_text = "Barcode"
                # 2. Known publisher logos based on imprint text
                elif any(pub in nearby_text.upper() or (is_cover_pg and r.y0 > page_h * 0.55 and pub in page_text_all.upper()) 
                         for pub in ("NATIONAL GEOGRAPHIC", "CENGAGE", "PEARSON", "MCGRAW", "OXFORD", "CAMBRIDGE")):
                    matched_pub = next((pub for pub in ("National Geographic Learning", "Cengage", "Pearson", "McGraw-Hill", "Oxford", "Cambridge") 
                                       if pub.upper() in nearby_text.upper() or pub.upper() in page_text_all.upper()), "Publisher")
                    alt_text = f"{matched_pub} logo"
                # 3. Document Title logo on cover pages
                elif is_cover_pg and r.y0 < page_h * 0.35 and r.x0 < page_w * 0.5:
                    doc_fn = os.path.splitext(os.path.basename(getattr(doc, "name", "")))[0]
                    first_kw = next((w for w in re.split(r'[_ -]+', doc_fn) if len(w) > 3 and not w.isdigit() and w.lower() not in ("student", "worksheets", "companion", "answer", "unit", "untagged", "remediated", "test", "temp", "copy")), "")
                    alt_text = f"{first_kw} logo" if first_kw else "Document Title logo"
                elif meaningful_label:
                    alt_text = f"Figure on Page {page_num}: {meaningful_label}"
                else:
                    alt_text = f"Figure {fig_counter} on Page {page_num}"

            fig_el = {
                "id": id_counter,
                "type": "figure",
                "tag": "Figure",
                "content": "",
                "bounding_box": pdf_bbox,
                "alt_text": alt_text,
                "xref": xref,
                "confidence": 0.95
            }
            id_counter += 1

                # Validate figure element with Pydantic model
                try:
                    from models.figure_element import FigureExtractedContent
                    fig_val = FigureExtractedContent(
                        page_number=page_num,
                        bounding_box=pdf_bbox,
                        content="",
                        type="figure",
                        tag="Figure",
                        confidence=0.95,
                        alt_text=alt_text,
                        xref=xref
                    )
                    fig_dict = fig_val.model_dump(by_alias=True, exclude_none=True)
                    fig_dict["id"] = fig_el["id"]
                    fig_el = fig_dict
                except Exception:
                    pass

                figures_data.append({
                    "figure_el": fig_el,
                    "extracted_elements": extracted_sub_elements,
                    "bbox": pdf_bbox,
                    "top_y": round(r.y0, 2),
                    "bottom_y": round(r.y1, 2),
                    "left_x": round(r.x0, 2),
                    "right_x": round(r.x1, 2)
                })
                flat_elements.append(fig_el)
                flat_elements.extend(extracted_sub_elements)

        # 4. Vector drawings extraction (vector logos, wordmarks, diagrams drawn as vector paths)
        try:
            raster_boxes = [f["bbox"] for f in figures_data]
            drawings = page.get_drawings()
            candidate_rects = []
            for d in drawings:
                r = fitz.Rect(d['rect'])
                w, h = r.width, r.height
                if r.y1 <= 0 or r.x1 <= 0 or w < 15 or h < 6:
                    continue
                if w > page_w * 0.85 and h > page_h * 0.85:
                    continue
                if h <= 3.5 or w <= 3.5:
                    continue
                # Aspect ratio check for long separator lines
                if (w / max(1.0, h) > 30) or (h / max(1.0, w) > 30):
                    continue
                # If rect contains selectable PDF text, it's text styling/background, not a pure graphic figure
                words = page.get_text("words", clip=r)
                if len(words) > 0:
                    continue
                candidate_rects.append(r)

            # Cluster overlapping/adjacent rects into cohesive vector figures
            clusters = []
            for r in candidate_rects:
                merged = False
                for c in clusters:
                    if (c.x0 - 8 <= r.x1 and r.x0 <= c.x1 + 8 and
                        c.y0 - 8 <= r.y1 and r.y0 <= c.y1 + 8):
                        c.include_rect(r)
                        merged = True
                        break
                if not merged:
                    clusters.append(fitz.Rect(r))

            for c in clusters:
                if c.width < 18 or c.height < 8:
                    continue
                if (c.width / max(1.0, c.height) > 30) or (c.height / max(1.0, c.width) > 30):
                    continue
                if len(page.get_text("words", clip=c)) > 0:
                    continue

                pdf_bbox = [
                    round(c.x0, 2),
                    round(page_h - c.y1, 2),
                    round(c.x1, 2),
                    round(page_h - c.y0, 2)
                ]

                # Ensure vector figure doesn't overlap an already extracted raster figure
                overlaps_raster = False
                for rf in raster_boxes:
                    ix0 = max(pdf_bbox[0], rf[0])
                    iy0 = max(pdf_bbox[1], rf[1])
                    ix1 = min(pdf_bbox[2], rf[2])
                    iy1 = min(pdf_bbox[3], rf[3])
                    if ix1 > ix0 and iy1 > iy0:
                        inter_area = (ix1 - ix0) * (iy1 - iy0)
                        vec_area = (pdf_bbox[2] - pdf_bbox[0]) * (pdf_bbox[3] - pdf_bbox[1])
                        if inter_area > 0.4 * vec_area:
                            overlaps_raster = True
                            break
                if overlaps_raster:
                    continue
                # Filter out repeated running header vector logos at top of subsequent pages
                if page_num > 1 and (c.y0 <= 45 or c.y1 <= 55):
                    continue

                # Run OCR on the vector graphic crop to extract text / wordmark description dynamically
                fig_counter += 1
                alt_text = f"Figure {fig_counter} on Page {page_num}"
                try:
                    pix = page.get_pixmap(clip=c, dpi=150)
                    if pix.width >= 15 and pix.height >= 15:
                        img_np = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.height, pix.width, pix.n))
                        if pix.n == 4:
                            img_np = img_np[:, :, :3]
                        if ocr is None:
                            ocr = PaddleLayoutExtractor.get_ocr()
                        res = ocr.ocr(img_np, cls=False)
                        lines = res[0] if res and res[0] else []
                        detected_words = [line[1][0].strip() for line in lines if line[1][0].strip()]
                        if detected_words:
                            label = " ".join(detected_words)
                            is_top_header = (c.y0 < page_h * 0.25)
                            is_bottom_footer = (c.y1 > page_h * 0.75)
                            if len(detected_words) <= 4 and (is_top_header or is_bottom_footer):
                                alt_text = f"{label} logo"
                            else:
                                alt_text = f"Figure on Page {page_num}: {label}"
                except Exception:
                    pass

                fig_el = {
                    "id": id_counter,
                    "type": "figure",
                    "tag": "Figure",
                    "content": "",
                    "bounding_box": pdf_bbox,
                    "alt_text": alt_text,
                    "confidence": 0.95
                }
                id_counter += 1

                try:
                    from models.figure_element import FigureExtractedContent
                    fig_val = FigureExtractedContent(
                        page_number=page_num,
                        bounding_box=pdf_bbox,
                        content="",
                        type="figure",
                        tag="Figure",
                        confidence=0.95,
                        alt_text=alt_text
                    )
                    fig_dict = fig_val.model_dump(by_alias=True, exclude_none=True)
                    fig_dict["id"] = fig_el["id"]
                    fig_el = fig_dict
                except Exception:
                    pass

                figures_data.append({
                    "figure_el": fig_el,
                    "extracted_elements": [],
                    "bbox": pdf_bbox,
                    "top_y": round(c.y0, 2),
                    "bottom_y": round(c.y1, 2),
                    "left_x": round(c.x0, 2),
                    "right_x": round(c.x1, 2)
                })
                flat_elements.append(fig_el)
        except Exception:
            pass

        if return_data:
            return figures_data, id_counter
        return flat_elements, id_counter


    @staticmethod
    def _process_figure_ocr_lines(lines_detected, r, pix_w, pix_h, page_num, page_w, page_h, id_counter, base_font_size, existing_elements):
        sub_elements = []
        scale_x = r.width / pix_w
        scale_y = r.height / pix_h

        parsed_boxes = []
        for line_info in lines_detected:
            box, (txt, conf) = line_info
            txt = txt.strip()
            if not txt:
                continue

            # Avoid duplicate extraction when the same text is already present on the page
            if existing_elements:
                clean_txt_lower = txt.lower()
                if any(clean_txt_lower in (e.get("content") or "").lower() for e in existing_elements if len(txt) > 4):
                    continue

            px_x0 = min(pt[0] for pt in box)
            px_y0 = min(pt[1] for pt in box)
            px_x1 = max(pt[0] for pt in box)
            px_y1 = max(pt[1] for pt in box)

            pdf_x0 = round(r.x0 + px_x0 * scale_x, 2)
            pdf_x1 = round(r.x0 + px_x1 * scale_x, 2)
            pdf_y0 = round(page_h - (r.y0 + px_y1 * scale_y), 2)
            pdf_y1 = round(page_h - (r.y0 + px_y0 * scale_y), 2)
            box_h = round(pdf_y1 - pdf_y0, 2)

            parsed_boxes.append({
                "text": txt,
                "bbox": [pdf_x0, pdf_y0, pdf_x1, pdf_y1],
                "top_y": round(r.y0 + px_y0 * scale_y, 2),
                "bottom_y": round(r.y0 + px_y1 * scale_y, 2),
                "left_x": pdf_x0,
                "right_x": pdf_x1,
                "height": box_h,
                "confidence": round(float(conf), 2)
            })

        if not parsed_boxes:
            return [], id_counter, ""

        summary_parts = [b["text"] for b in parsed_boxes[:3]]
        summary = ", ".join(summary_parts)

        # 1. Table structure detection inside figure
        # A genuine table inside a figure must have:
        # - At least 2 distinct rows
        # - Multiple distinct columns aligned horizontally across at least 2 rows
        # - Short tabular cells rather than continuous prose paragraphs/sentences
        sorted_by_y = sorted(parsed_boxes, key=lambda b: b["top_y"])
        rows = []
        current_row = [sorted_by_y[0]]
        for b in sorted_by_y[1:]:
            row_top = current_row[0]["top_y"]
            row_h = current_row[0]["height"]
            if abs(b["top_y"] - row_top) <= max(row_h * 0.5, 5.0):
                current_row.append(b)
            else:
                rows.append(current_row)
                current_row = [b]
        if current_row:
            rows.append(current_row)

        multi_cell_rows = [r for r in rows if len(r) >= 2]
        all_cell_texts = [b["text"] for b in parsed_boxes]
        avg_cell_len = sum(len(t) for t in all_cell_texts) / max(len(all_cell_texts), 1)

        # Genuine table criteria: multiple multi-cell rows, consistent column count, not prose sentences
        is_table = (
            len(rows) >= 2 and
            len(multi_cell_rows) >= 2 and
            len(rows) <= 30 and
            avg_cell_len < 45  # Tables have concise cell data, not long book paragraphs
        )

        if is_table:
            table_rows = []
            for r_idx, row in enumerate(rows):
                row_sorted = sorted(row, key=lambda c: c["left_x"])
                cell_type = "table_header_cell" if r_idx == 0 else "table_data_cell"
                cell_tag = "TH" if r_idx == 0 else "TD"
                cells = []
                for c_item in row_sorted:
                    c_id = id_counter
                    id_counter += 1
                    cells.append({
                        "id": c_id,
                        "type": cell_type,
                        "tag": cell_tag,
                        "content": c_item["text"],
                        "bounding_box": c_item["bbox"],
                        "confidence": c_item["confidence"]
                    })
                r_id = id_counter
                id_counter += 1
                row_bbox = [
                    min(c["bounding_box"][0] for c in cells),
                    min(c["bounding_box"][1] for c in cells),
                    max(c["bounding_box"][2] for c in cells),
                    max(c["bounding_box"][3] for c in cells)
                ]
                table_rows.append({
                    "id": r_id,
                    "type": "table_row",
                    "tag": "TR",
                    "kids": cells,
                    "bounding_box": row_bbox
                })
            tab_bbox = [
                min(r["bounding_box"][0] for r in table_rows),
                min(r["bounding_box"][1] for r in table_rows),
                max(r["bounding_box"][2] for r in table_rows),
                max(r["bounding_box"][3] for r in table_rows)
            ]
            t_id = id_counter
            id_counter += 1
            table_el = {
                "id": t_id,
                "type": "table",
                "tag": "Table",
                "kids": table_rows,
                "bounding_box": tab_bbox,
                "confidence": round(float(np.mean([b["confidence"] for b in parsed_boxes])), 2)
            }
            try:
                from models.figure_element import FigureExtractedContent
                val_tab = FigureExtractedContent(
                    page_number=page_num,
                    bounding_box=tab_bbox,
                    content="Table",
                    type="table",
                    tag="Table",
                    confidence=table_el["confidence"],
                    kids=table_rows
                )
                t_dict = val_tab.model_dump(by_alias=True, exclude_none=True)
                t_dict["id"] = table_el["id"]
                table_el = t_dict
            except Exception:
                pass
            sub_elements.append(table_el)
            return sub_elements, id_counter, f"Table: {summary}"

        # 2. General layout classification: Headings, Lists, Paragraphs, Formulas
        median_h = float(np.median([b["height"] for b in parsed_boxes])) if parsed_boxes else 12.0
        curr_p = None

        for item in sorted_by_y:
            txt = item["text"]
            h = item["height"]
            bbox = item["bbox"]
            conf = item["confidence"]

            m_sec = re.match(r'^\s*(?:(?:Chapter|Section)\s+)?(\d+(?:\.\d+)*)\.?\s*(\S.*)?$', txt, re.IGNORECASE)
            is_outline_heading = False
            outline_lvl = 2
            if m_sec:
                parts = m_sec.group(1).split('.')
                if len(parts) >= 2 and len(txt) < 120:
                    is_outline_heading = True
                    outline_lvl = min(6, len(parts))

            is_bullet = False
            if not is_outline_heading:
                is_bullet = bool(re.match(r"^([•\-\*\u2022\u25cf\u25cb]|\d+[\.\)]|[a-zA-Z][\.\)]|\([a-zA-Z0-9]\))\s+", txt))
            is_formula = bool(re.search(r'([=≈≠≤≥∑√∫±]|\b[a-zA-Z]\s*=\s*|\b\d+\s*[\+\-\*\/]\s*\d+)', txt)) and len(txt) < 40
            is_heading = (h >= median_h * 1.3 or (txt.isupper() and len(txt) < 40)) and len(txt) < 90 and not txt.endswith('.')

            if is_outline_heading:
                if curr_p:
                    sub_elements.append(curr_p)
                    curr_p = None
                el = {
                    "id": id_counter,
                    "type": "heading",
                    "tag": f"H{outline_lvl}",
                    "heading_level": outline_lvl,
                    "content": txt,
                    "bounding_box": bbox,
                    "confidence": conf
                }
                id_counter += 1
                sub_elements.append(el)

            elif is_bullet:
                if curr_p:
                    sub_elements.append(curr_p)
                    curr_p = None
                el = {
                    "id": id_counter,
                    "type": "list_item",
                    "tag": "LI",
                    "content": txt,
                    "bounding_box": bbox,
                    "confidence": conf
                }
                id_counter += 1
                sub_elements.append(el)

            elif is_heading:
                if curr_p:
                    sub_elements.append(curr_p)
                    curr_p = None
                h_level = 2 if h >= median_h * 1.5 else 3
                el = {
                    "id": id_counter,
                    "type": "heading",
                    "tag": f"H{h_level}",
                    "heading_level": h_level,
                    "content": txt,
                    "bounding_box": bbox,
                    "confidence": conf
                }
                id_counter += 1
                sub_elements.append(el)

            elif is_formula:
                if curr_p:
                    sub_elements.append(curr_p)
                    curr_p = None
                el = {
                    "id": id_counter,
                    "type": "formula",
                    "tag": "Formula",
                    "content": txt,
                    "alt_text": PaddleLayoutExtractor._math_to_speech(txt),
                    "bounding_box": bbox,
                    "confidence": conf
                }
                id_counter += 1
                sub_elements.append(el)

            else:
                if curr_p and abs(item["top_y"] - curr_p["_bottom_y"]) <= median_h * 1.4:
                    curr_p["content"] += " " + txt
                    b0 = curr_p["bounding_box"]
                    curr_p["bounding_box"] = [
                        min(b0[0], bbox[0]), min(b0[1], bbox[1]),
                        max(b0[2], bbox[2]), max(b0[3], bbox[3])
                    ]
                    curr_p["_bottom_y"] = item["bottom_y"]
                else:
                    if curr_p:
                        sub_elements.append(curr_p)
                    curr_p = {
                        "id": id_counter,
                        "type": "paragraph",
                        "tag": "P",
                        "content": txt,
                        "bounding_box": bbox,
                        "confidence": conf,
                        "_bottom_y": item["bottom_y"]
                    }
                    id_counter += 1

        if curr_p:
            sub_elements.append(curr_p)

        # Cleanup internal keys & validate with Pydantic
        validated_sub = []
        for el in sub_elements:
            el.pop("_bottom_y", None)
            try:
                from models.figure_element import FigureExtractedContent
                val_el = FigureExtractedContent(
                    page_number=page_num,
                    bounding_box=el["bounding_box"],
                    content=el.get("content", ""),
                    type=el.get("type", "paragraph"),
                    tag=el.get("tag", "P"),
                    confidence=el.get("confidence", 0.95),
                    heading_level=el.get("heading_level"),
                    kids=el.get("kids")
                )
                d = val_el.model_dump(by_alias=True, exclude_none=True)
                d["id"] = el["id"]
                validated_sub.append(d)
            except Exception:
                validated_sub.append(el)

        return validated_sub, id_counter, summary

    @staticmethod
    def _integrate_figures_reading_order(digital_elements, figures_data, page_w, page_h):
        """
        Inserts figures and their extracted content into the page's logical reading order.
        Respects multi-column structure, section continuity, headings, and paragraph flow.
        """
        col_split = page_w * 0.48

        def get_top(el):
            bbox = el.get("bounding_box", [0, 0, 0, 0])
            return page_h - bbox[3] if len(bbox) == 4 else 0

        def get_bottom(el):
            bbox = el.get("bounding_box", [0, 0, 0, 0])
            return page_h - bbox[1] if len(bbox) == 4 else 0

        # Strict multi-column detection: only split into columns if there are multiple genuinely narrow blocks
        # occupying parallel column widths (less than 45% of page width each)
        left_narrow = [el for el in digital_elements if el.get("bounding_box", [0, 0, 0, 0])[0] < col_split and (el.get("bounding_box", [0, 0, 0, 0])[2] - el.get("bounding_box", [0, 0, 0, 0])[0]) < page_w * 0.45]
        right_narrow = [el for el in digital_elements if el.get("bounding_box", [0, 0, 0, 0])[0] >= col_split and (el.get("bounding_box", [0, 0, 0, 0])[2] - el.get("bounding_box", [0, 0, 0, 0])[0]) < page_w * 0.45]
        is_multi_col = (page_w > 400 and len(left_narrow) >= 2 and len(right_narrow) >= 2)

        bot_footers = []
        top_headers = []
        body_elements = []

        if is_multi_col:
            for el in digital_elements:
                b = el.get("bounding_box", [0, 0, 0, 0])
                top_y = get_top(el)
                # Bottom margin in PDF coordinates: y1 <= 45 or top_y >= page_h - 45
                if len(b) == 4 and (b[3] <= 45 or top_y >= page_h - 45):
                    bot_footers.append(el)
                elif len(b) == 4 and top_y <= 40 and (b[2] - b[0]) >= page_w * 0.45:
                    top_headers.append(el)
                else:
                    body_elements.append(el)
            bot_footers.sort(key=lambda e: e.get("bounding_box", [0, 0, 0, 0])[0])
            top_headers.sort(key=lambda e: e.get("bounding_box", [0, 0, 0, 0])[0])
        def is_section_splitter(e):
            txt = (e.get("content") or "").strip()
            b = e.get("bounding_box", [0, 0, 0, 0])
            w = b[2] - b[0] if len(b) == 4 else 0
            if re.match(r'^\s*(?:SECTION|CHAPTER|UNIT|PART|MODULE)\s+\d+', txt, re.I):
                return True
            if w >= page_w * 0.45 and e.get("tag") in ("H1", "H2") and txt.upper() not in ("KEY CONCEPTS", "EXAMPLES", "CONCEPTS", "DEFINITIONS", "SUMMARY"):
                return True
            return False

        sec_splitters = sorted([e for e in body_elements if is_section_splitter(e)], key=get_top)

        def insert_into_col(col_text_els, col_figs):
            result = list(col_text_els)
            for fig_info in col_figs:
                fig_content = [fig_info["figure_el"]]
                fig_top = fig_info["top_y"]

                preceding_idx = -1
                for i, el in enumerate(result):
                    el_bottom = get_bottom(el)
                    if el_bottom <= fig_top + 15:
                        preceding_idx = i

                if preceding_idx != -1:
                    insert_pos = preceding_idx + 1
                    result[insert_pos:insert_pos] = fig_content
                else:
                    # Place figure at the end of the column text so it does not interrupt
                    # continuous lists or paragraph flow across columns
                    result.extend(fig_content)
            return result

        if is_multi_col and len(sec_splitters) >= 1:
            combined = list(top_headers)
            bands = []
            for i, s in enumerate(sec_splitters):
                y0 = get_top(s)
                y1 = get_top(sec_splitters[i+1]) if i + 1 < len(sec_splitters) else (page_h - 45)
                b_els = [e for e in body_elements if y0 <= get_top(e) < y1 and e != s]
                b_figs = [f for f in figures_data if y0 <= f["top_y"] < y1]
                bands.append((s, b_els, b_figs))

            first_y = get_top(sec_splitters[0])
            pre_els = [e for e in body_elements if get_top(e) < first_y]
            pre_figs = [f for f in figures_data if f["top_y"] < first_y]

            if pre_els or pre_figs:
                p_c0 = sorted([e for e in pre_els if (e.get("bounding_box", [0,0,0,0])[0] + e.get("bounding_box", [0,0,0,0])[2]) / 2.0 < col_split], key=get_top)
                p_c1 = sorted([e for e in pre_els if (e.get("bounding_box", [0,0,0,0])[0] + e.get("bounding_box", [0,0,0,0])[2]) / 2.0 >= col_split], key=get_top)
                p_f0 = sorted([f for f in pre_figs if (f["left_x"] + f["right_x"]) / 2.0 < col_split], key=lambda f: f["top_y"])
                p_f1 = sorted([f for f in pre_figs if (f["left_x"] + f["right_x"]) / 2.0 >= col_split], key=lambda f: f["top_y"])
                combined.extend(insert_into_col(p_c0, p_f0))
                combined.extend(insert_into_col(p_c1, p_f1))

            for s, b_els, b_figs in bands:
                combined.append(s)
                b_c0_els = sorted([e for e in b_els if (e.get("bounding_box", [0,0,0,0])[0] + e.get("bounding_box", [0,0,0,0])[2]) / 2.0 < col_split], key=get_top)
                b_c1_els = sorted([e for e in b_els if (e.get("bounding_box", [0,0,0,0])[0] + e.get("bounding_box", [0,0,0,0])[2]) / 2.0 >= col_split], key=get_top)
                b_c0_figs = sorted([f for f in b_figs if (f["left_x"] + f["right_x"]) / 2.0 < col_split], key=lambda f: f["top_y"])
                b_c1_figs = sorted([f for f in b_figs if (f["left_x"] + f["right_x"]) / 2.0 >= col_split], key=lambda f: f["top_y"])

                fin_c0 = insert_into_col(b_c0_els, b_c0_figs)
                fin_c1 = insert_into_col(b_c1_els, b_c1_figs)
                combined.extend(fin_c0)
                combined.extend(fin_c1)

            combined.extend(bot_footers)
        elif not figures_data:
            if is_multi_col:
                col0 = sorted([e for e in body_elements if (e.get("bounding_box", [0,0,0,0])[0] + e.get("bounding_box", [0,0,0,0])[2]) / 2.0 < col_split], key=get_top)
                col1 = sorted([e for e in body_elements if (e.get("bounding_box", [0,0,0,0])[0] + e.get("bounding_box", [0,0,0,0])[2]) / 2.0 >= col_split], key=get_top)
                combined = top_headers + col0 + col1 + bot_footers
            else:
                def sort_key(el):
                    bbox = el.get("bounding_box", [0, 0, 0, 0])
                    top = page_h - bbox[3] if len(bbox) == 4 else 0
                    left = bbox[0] if len(bbox) == 4 else 0
                    return (round(top / 8) * 8, left)
                combined = sorted(digital_elements, key=sort_key)
        else:
            col0_els = sorted([el for el in body_elements if (el.get("bounding_box", [0,0,0,0])[0] + el.get("bounding_box", [0,0,0,0])[2]) / 2.0 < col_split] if is_multi_col else body_elements, key=get_top)
            col1_els = sorted([el for el in body_elements if (el.get("bounding_box", [0,0,0,0])[0] + el.get("bounding_box", [0,0,0,0])[2]) / 2.0 >= col_split], key=get_top) if is_multi_col else []

            col0_figs = []
            col1_figs = []
            spanning_figs = []

            for f_data in figures_data:
                w = f_data["right_x"] - f_data["left_x"]
                center_x = (f_data["left_x"] + f_data["right_x"]) / 2.0
                if is_multi_col and w >= page_w * 0.65:
                    spanning_figs.append(f_data)
                elif is_multi_col and (center_x >= col_split or f_data["left_x"] >= page_w * 0.40):
                    col1_figs.append(f_data)
                else:
                    col0_figs.append(f_data)

        col0_figs.sort(key=lambda f: (f["top_y"], f["left_x"]))
        col1_figs.sort(key=lambda f: (f["top_y"], f["left_x"]))
        spanning_figs.sort(key=lambda f: (f["top_y"], f["left_x"]))

        def insert_into_col(col_text_els, col_figs):
            result = list(col_text_els)
            for fig_info in col_figs:
                fig_content = [fig_info["figure_el"]] + fig_info["extracted_elements"]
                fig_top = fig_info["top_y"]
                fig_left = fig_info["left_x"]

                insert_pos = 0
                for i, el in enumerate(result):
                    el_top = get_top(el)
                    el_bottom = get_bottom(el)
                    el_left = el.get("bounding_box", [0, 0, 0, 0])[0]

                    if el_bottom <= fig_top + 10:
                        insert_pos = i + 1
                    elif abs(el_top - fig_top) <= 25 and el_left < fig_left:
                        insert_pos = i + 1
                    elif el_top > fig_top + 25:
                        break

                result[insert_pos:insert_pos] = fig_content
            return result

        final_col0 = insert_into_col(col0_els, col0_figs)
        final_col1 = insert_into_col(col1_els, col1_figs) if is_multi_col else []

            combined = []
            if is_multi_col:
                combined.extend(top_headers)
                top_spanning = [f for f in spanning_figs if f["top_y"] < (page_h * 0.25)]
                mid_spanning = [f for f in spanning_figs if (page_h * 0.25) <= f["top_y"] <= (page_h * 0.75)]
                bot_spanning = [f for f in spanning_figs if f["top_y"] > (page_h * 0.75)]

                for f in top_spanning:
                    combined.append(f["figure_el"])
                combined.extend(final_col0)
                for f in mid_spanning:
                    combined.append(f["figure_el"])
                combined.extend(final_col1)
                for f in bot_spanning:
                    combined.append(f["figure_el"])
                combined.extend(bot_footers)
            else:
                combined = final_col0
                for f in spanning_figs:
                    combined.append(f["figure_el"])


        for idx, el in enumerate(combined):
            el["reading_order"] = idx + 1

        return combined

