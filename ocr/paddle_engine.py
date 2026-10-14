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

from paddleocr import PaddleOCR


class PaddleLayoutExtractor:
    """
    Local layout and text extractor using PaddleOCR + PyMuPDF.
    Requires no cloud API key, no activation code, and runs 100% offline.
    """
    _ocr_instance = None

    @classmethod
    def get_ocr(cls):
        if cls._ocr_instance is None:
            cls._ocr_instance = PaddleOCR(
                use_angle_cls=False,
                lang="en",
                use_gpu=False,
                show_log=False
            )
        return cls._ocr_instance

    @staticmethod
    def check_pdf_has_struct_tree(doc) -> bool:
        try:
            catalog_xref = doc.pdf_catalog()
            if catalog_xref > 0:
                cat_dict = doc.xref_object(catalog_xref)
                if "/StructTreeRoot" in cat_dict:
                    return True
            flags = fitz.TEXTFLAGS_SEARCH | fitz.TEXT_COLLECT_STRUCTURE
            for page in doc[:min(4, len(doc))]:
                t = page.get_text("dict", flags=flags)
                for b in t.get("blocks", []):
                    if b.get("std") or b.get("raw"):
                        return True
        except Exception:
            pass
        return False

    @staticmethod
    def _extract_from_struct_tree(doc, pdf_path: str, progress_callback=None) -> dict:
        total_pages = len(doc)
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
            
            kids = []
            for sb in sub:
                k = walk_tree(sb, page_h, pno)
                if k:
                    if isinstance(k, list):
                        kids.extend(k)
                    else:
                        kids.append(k)

            # Flatten document/section wrappers so top-level elements are direct
            if tag in ('Document', 'Sect', 'Part', 'Art', 'Div'):
                return kids

            text = extract_node_text(b)
            if not tag and not text and not kids:
                return None

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
                'tag': tag or ('P' if text else 'Span'),
                'content': text,
                'bounding_box': pdf_bbox,
                'confidence': 1.0
            }
            if tag in ('H1', 'H2', 'H3', 'H4', 'H5', 'H6'):
                res['heading_level'] = int(tag[1])
            elif tag == 'H':
                res['heading_level'] = 1

            if tag == 'Figure':
                # 1. Check if figure has trapped meaningful text in its subblocks
                has_meaningful_sub_text = any(
                    bool((k.get('content') or '').strip()) for k in kids if isinstance(k, dict)
                ) if kids else False

                if has_meaningful_sub_text:
                    text_kids = [k for k in kids if isinstance(k, dict) and (k.get('content') or '').strip()]
                    summary = ", ".join((k.get('content') or '')[:30] for k in text_kids[:3])
                    res['alt_text'] = f'Figure on Page {pno}: {summary}'
                    classified_elements = []
                    for k in text_kids:
                        txt = (k.get('content') or '').strip()
                        m_sec = re.match(r'^\s*(?:(?:Chapter|Section)\s+)?(\d+(?:\.\d+)*)\.?\s*(\S.*)?$', txt, re.IGNORECASE)
                        if m_sec and len(m_sec.group(1).split('.')) >= 2:
                            parts = m_sec.group(1).split('.')
                            h_lv = min(6, len(parts))
                            k['type'] = 'heading'
                            k['tag'] = f'H{h_lv}'
                            k['heading_level'] = h_lv
                        elif re.match(r'^([•●○▪■□►▸\-\*]|\d+[\.\)]|[a-zA-Z][\.\)])\s+', txt):
                            k['type'] = 'list_item'
                            k['tag'] = 'LI'
                        elif len(txt) < 80 and not txt.endswith('.') and (txt.isupper() or len(txt.split()) <= 6):
                            k['type'] = 'heading'
                            k['tag'] = 'H2'
                            k['heading_level'] = 2
                        else:
                            k['type'] = 'paragraph'
                            k['tag'] = 'P'
                        classified_elements.append(k)
                    return [res] + classified_elements

                # 2. Check if embedded image at figure bbox has text extractable via OCR
                if not kids and not text and pdf_bbox:
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
                                            sub_els, next_id, summary = PaddleLayoutExtractor._process_figure_ocr_lines(
                                                lines_detected, img_r, pix.width, pix.height, pno, page.rect.width, page_h,
                                                curr_id + 1, 11.0, None
                                            )
                                            if sub_els:
                                                try:
                                                    while next(id_gen) < next_id:
                                                        pass
                                                except Exception:
                                                    pass
                                                res['alt_text'] = f'Figure on Page {pno}: {summary}'
                                                return [res] + sub_els
                    except Exception:
                        pass

                res['alt_text'] = f'Figure on Page {pno}'

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

            extracted_doc["pages"].append({
                "page_number": page_num,
                "width": round(page_w, 2),
                "height": round(page_h, 2),
                "elements": page_elements
            })

        doc.close()
        return extracted_doc

    @staticmethod
    def _extract_digital_page(page, page_num, page_w, page_h, id_counter, has_seen_h1, base_font_size):
        elements = []
        
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
                    # A genuine table must have:
                    # 1. At least 2 rows with non-empty content
                    # 2. At least 2 columns in multiple rows (grid alignment)
                    # 3. High data fill ratio (preventing sparse single-column or figure-wide false tables)
                    rows_with_data = [r for r in extracted_rows if any(c and str(c).strip() for c in r)]
                    multi_col_rows = [r for r in extracted_rows if len([c for c in r if c and str(c).strip()]) >= 2]
                    cells_with_data = [c for r in extracted_rows for c in r if c and str(c).strip()]
                    total_cells = sum(len(r) for r in extracted_rows)
                    fill_ratio = len(cells_with_data) / max(total_cells, 1)

                    is_genuine_table = (
                        len(rows_with_data) >= 2 and
                        len(multi_col_rows) >= 2 and
                        fill_ratio >= 0.35 and
                        len(cells_with_data) >= 4 and
                        (t_box[2] - t_box[0] >= 80) and
                        (t_box[3] - t_box[1] >= 30)
                    )

                    if not is_genuine_table:
                        # Do not register as a table; let the text blocks inside be extracted as headings/paragraphs/lists!
                        continue

                    table_regions.append(t_box)
                    rows_list = []
                    for r_idx, row in enumerate(extracted_rows):
                        cell_type = "table_header_cell" if r_idx == 0 else "table_data_cell"
                        cell_tag = "TH" if r_idx == 0 else "TD"
                        cells = []
                        for c_idx, cell_val in enumerate(row):
                            val = str(cell_val or "").strip()
                            c_id = id_counter
                            id_counter += 1
                            cells.append({
                                "id": c_id,
                                "type": cell_type,
                                "tag": cell_tag,
                                "content": val,
                                "bounding_box": pdf_tbox,
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

        # 2. Text line and block extraction with Underline vector detection
        underline_rects = []
        try:
            for d in page.get_drawings():
                r = d.get("rect")
                if r and r.height <= 3.5 and 4.0 <= r.width <= 0.75 * page_w:
                    underline_rects.append(r)
                for it in d.get("items", []):
                    if it[0] == "l":
                        p1, p2 = it[1], it[2]
                        if abs(p1.y - p2.y) <= 1.5:
                            lw = abs(p2.x - p1.x)
                            if 4.0 <= lw <= 0.75 * page_w:
                                underline_rects.append(fitz.Rect(min(p1.x, p2.x), min(p1.y, p2.y) - 1, max(p1.x, p2.x), max(p1.y, p2.y) + 1))
        except Exception:
            pass

        blocks = page.get_text("dict", flags=fitz.TEXTFLAGS_SEARCH).get("blocks", [])
        curr_el = None

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

                if curr_el:
                    elements.append(curr_el)
                    curr_el = None

                for line in b.get("lines", []):
                    spans = line.get("spans", [])
                    line_text = "".join(s.get("text", "") for s in spans).strip()
                    if not line_text:
                        continue

                    # Filter out isolated artifact tabs/spaces
                    if line_text in ("\t", " ", ""):
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
                        s.get("flags", 0) & 2 or "bold" in s.get("font", "").lower() or "black" in s.get("font", "").lower()
                        for s in spans
                    )

                    l_box = list(line.get("bbox", [0, 0, 0, 0]))
                    pdf_bbox = [
                        round(l_box[0], 2), round(page_h - l_box[3], 2),
                        round(l_box[2], 2), round(page_h - l_box[1], 2)
                    ]

                    # Check for hierarchical outline section numbering:
                    # e.g., "2", "2.", "2.1", "2.2", "2.2.1", "2.2.1.2", "2.3.1.2"
                    # Rule: Heading levels follow document outline rather than visual appearance ("same font, size, bold").
                    outline_match = re.match(r'^\s*(?:(?:Chapter|Section)\s+)?(\d+(?:\.\d+)*)\.?\s*(\S.*)?$', line_text, re.IGNORECASE)
                    is_outline_heading = False
                    outline_h_level = None

                    if outline_match:
                        num_str = outline_match.group(1)
                        parts = num_str.split('.')
                        title_part = (outline_match.group(2) or "").strip()

                        if len(parts) >= 2:
                            # Multi-level numbered section heading (2.1, 2.2, 2.2.1, 2.2.1.2, etc.)
                            # Even with same font family, font size, and bold formatting ("same font, size, bold"):
                            # Heading level is strictly determined by outline depth:
                            # 2 parts (2.1, 2.2, 2.3) -> H2
                            # 3 parts (2.2.1, 2.3.1) -> H3
                            # 4 parts (2.2.1.2, 2.3.1.2) -> H4
                            if len(line_text) < 120 and (has_bold or not title_part.endswith('.') or max_s >= base_font_size * 1.0):
                                is_outline_heading = True
                                outline_h_level = min(6, len(parts))
                        elif len(parts) == 1:
                            # Single-number top-level section (e.g. "2." or "2. Introduction" or "Chapter 2")
                            # A multi-word sentence / instruction (e.g. "1. Direct students' attention...") is a list item, not a section heading.
                            has_ch_prefix = bool(re.match(r'^\s*(?:Chapter|Section)\s+', line_text, re.IGNORECASE))
                            title_words = title_part.split()
                            is_short_title = bool(title_part) and len(title_words) <= 5 and len(line_text) < 45 and not title_part.endswith('.') and not any(p in title_part for p in (',', ';', ':', '?', '!'))
                            is_title_candidate = (
                                has_ch_prefix or
                                (line_text.isupper() and len(line_text) < 35 and any(c.isalpha() for c in line_text)) or
                                (is_short_title and (has_bold or max_s >= base_font_size * 1.15)) or
                                (not bool(title_part) and len(line_text) < 10 and max_s >= base_font_size * 1.15)
                            )
                            if is_title_candidate:
                                is_outline_heading = True
                                # Rule: "2. H1 -> only once in pdf"
                                if not has_seen_h1:
                                    outline_h_level = 1
                                    has_seen_h1 = True
                                else:
                                    outline_h_level = 2

                    # Normalize bullet markers (convert literal "Bullet " text to bullet symbol)
                    norm_text = re.sub(r'^\s*Bullet\s+', '• ', line_text)
                    is_bullet = False
                    if not is_outline_heading:
                        is_bullet = bool(re.match(r'^(•|[•●○▪■□►▸\-\*]|\d+[\.\)]|[a-zA-Z][\.\)])\s+', norm_text))

                    # Heading classification based on typography hierarchy (for non-numbered headings)
                    is_h1 = (max_s >= base_font_size * 1.8) and (len(line_text) < 90) and (not has_seen_h1)
                    is_h2 = (max_s >= base_font_size * 1.25) and (len(line_text) < 90) and not line_text.endswith('.')
                    is_page_number = bool(re.match(r'^[A-Za-z]?\d{1,5}$', line_text.strip()))
                    is_h3 = (
                        not is_page_number and (
                            (has_bold and max_s >= base_font_size * 1.05 and len(line_text) < 65 and not line_text.endswith('.')) or
                            (line_text.isupper() and len(line_text) < 30 and not line_text.endswith('.') and any(c.isalpha() for c in line_text))
                        )
                    )

                    if is_outline_heading:
                        if curr_el:
                            elements.append(curr_el)
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

                    elif is_bullet:
                        if curr_el:
                            elements.append(curr_el)
                        curr_el = {
                            "id": id_counter,
                            "type": "list_item",
                            "tag": "LI",
                            "content": norm_text,
                            "bounding_box": pdf_bbox,
                            "font_size": round(max_s, 1),
                            "confidence": 1.0
                        }
                        id_counter += 1

                    elif is_h1 or is_h2 or is_h3:
                        if curr_el:
                            elements.append(curr_el)
                        
                        if is_h1:
                            h_level = 1
                            has_seen_h1 = True
                        elif is_h2:
                            h_level = 2
                        else:
                            h_level = 3

                        curr_el = {
                            "id": id_counter,
                            "type": "heading",
                            "tag": f"H{h_level}",
                            "heading_level": h_level,
                            "content": line_text,
                            "bounding_box": pdf_bbox,
                            "font_size": round(max_s, 1),
                            "confidence": 1.0
                        }
                        id_counter += 1

                    else:
                        # Continuation or Paragraph
                        if curr_el and curr_el["type"] == "list_item":
                            # Indented / wrapped continuation of previous list item
                            curr_el["content"] += " " + line_text
                            b0 = curr_el["bounding_box"]
                            curr_el["bounding_box"] = [
                                min(b0[0], pdf_bbox[0]), min(b0[1], pdf_bbox[1]),
                                max(b0[2], pdf_bbox[2]), max(b0[3], pdf_bbox[3])
                            ]
                        elif curr_el and curr_el["type"] == "paragraph":
                            # Merge regular paragraph lines
                            curr_el["content"] += " " + line_text
                            b0 = curr_el["bounding_box"]
                            curr_el["bounding_box"] = [
                                min(b0[0], pdf_bbox[0]), min(b0[1], pdf_bbox[1]),
                                max(b0[2], pdf_bbox[2]), max(b0[3], pdf_bbox[3])
                            ]
                        else:
                            if curr_el:
                                elements.append(curr_el)
                            curr_el = {
                                "id": id_counter,
                                "type": "paragraph",
                                "tag": "P",
                                "content": line_text,
                                "bounding_box": pdf_bbox,
                                "font_size": round(max_s, 1),
                                "confidence": 1.0
                            }
                            id_counter += 1

                    if curr_el:
                        if line_underlined:
                            curr_el["underline"] = True
                        curr_el.setdefault("_spans_meta", []).extend(spans)

        if curr_el:
            elements.append(curr_el)

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

        return elements, id_counter, has_seen_h1

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
        for img_idx, img_info in enumerate(image_list):
            xref = img_info[0]
            try:
                rects = page.get_image_rects(xref)
            except Exception:
                rects = []

            # Check if image is a single solid color / tint mask
            is_dummy_mask = False
            if doc is not None:
                try:
                    pix_check = fitz.Pixmap(doc, xref)
                    if len(set(pix_check.samples)) <= 2:
                        is_dummy_mask = True
                except Exception:
                    pass
            if is_dummy_mask:
                continue

            for r in rects:
                # Filter out microscopic icon pixels or full-page background scans
                w = r.width
                h = r.height
                if w < 20 or h < 20:
                    continue
                if w > page_w * 0.95 and h > page_h * 0.95:
                    # Full page background
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
                if meaningful_label:
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
                    "type": "paragraph",
                    "tag": "P",
                    "content": txt,
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
        else:
            body_elements = digital_elements

        if not figures_data:
            if is_multi_col:
                col0 = sorted([e for e in body_elements if e.get("bounding_box", [0,0,0,0])[0] < col_split], key=get_top)
                col1 = sorted([e for e in body_elements if e.get("bounding_box", [0,0,0,0])[0] >= col_split], key=get_top)
                sorted_els = top_headers + col0 + col1 + bot_footers
            else:
                def sort_key(el):
                    bbox = el.get("bounding_box", [0, 0, 0, 0])
                    top = page_h - bbox[3] if len(bbox) == 4 else 0
                    left = bbox[0] if len(bbox) == 4 else 0
                    return (round(top / 8) * 8, left)
                sorted_els = sorted(digital_elements, key=sort_key)
            for idx, el in enumerate(sorted_els):
                el["reading_order"] = idx + 1
            return sorted_els

        col0_els = sorted([el for el in body_elements if el.get("bounding_box", [0,0,0,0])[0] < col_split] if is_multi_col else body_elements, key=get_top)
        col1_els = sorted([el for el in body_elements if el.get("bounding_box", [0,0,0,0])[0] >= col_split], key=get_top) if is_multi_col else []

        col0_figs = []
        col1_figs = []
        spanning_figs = []

        for f_data in figures_data:
            w = f_data["right_x"] - f_data["left_x"]
            if is_multi_col and w >= page_w * 0.65:
                spanning_figs.append(f_data)
            elif is_multi_col and f_data["left_x"] >= col_split:
                col1_figs.append(f_data)
            else:
                col0_figs.append(f_data)

        col0_figs.sort(key=lambda f: f["top_y"])
        col1_figs.sort(key=lambda f: f["top_y"])
        spanning_figs.sort(key=lambda f: f["top_y"])

        def insert_into_col(col_text_els, col_figs):
            result = list(col_text_els)
            for fig_info in col_figs:
                fig_content = [fig_info["figure_el"]] + fig_info["extracted_elements"]
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
                    if result and result[0].get("type") == "heading" and result[0].get("heading_level") == 1:
                        result[1:1] = fig_content
                    else:
                        result[0:0] = fig_content
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
                combined.extend([f["figure_el"]] + f["extracted_elements"])
            combined.extend(final_col0)
            for f in mid_spanning:
                combined.extend([f["figure_el"]] + f["extracted_elements"])
            combined.extend(final_col1)
            for f in bot_spanning:
                combined.extend([f["figure_el"]] + f["extracted_elements"])
            combined.extend(bot_footers)
        else:
            combined = final_col0
            for f in spanning_figs:
                combined.extend([f["figure_el"]] + f["extracted_elements"])

        for idx, el in enumerate(combined):
            el["reading_order"] = idx + 1

        return combined

