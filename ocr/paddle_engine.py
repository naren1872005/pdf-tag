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
                        if re.match(r'^([•●○▪■□►▸\-\*]|\d+[\.\)]|[a-zA-Z][\.\)])\s+', txt):
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
                    table_regions.append(t_box)
                    pdf_tbox = [
                        round(t_box[0], 2), round(page_h - t_box[3], 2),
                        round(t_box[2], 2), round(page_h - t_box[1], 2)
                    ]
                    extracted_rows = tab.extract()
                    # Filter out single-cell or border noise tables
                    if len(extracted_rows) < 1 or (len(extracted_rows) == 1 and len(extracted_rows[0]) <= 1):
                        continue
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

        # 2. Text line and block extraction
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

                for line in b.get("lines", []):
                    spans = line.get("spans", [])
                    line_text = "".join(s.get("text", "") for s in spans).strip()
                    if not line_text:
                        continue

                    # Filter out isolated artifact tabs/spaces
                    if line_text in ("\t", " ", ""):
                        continue

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

                    # Normalize bullet markers (convert literal "Bullet " text to bullet symbol)
                    norm_text = re.sub(r'^\s*Bullet\s+', '• ', line_text)
                    is_bullet = bool(re.match(r'^(•|[•●○▪■□►▸\-\*]|\d+[\.\)]|[a-zA-Z][\.\)])\s+', norm_text))

                    # Heading classification based on typography hierarchy
                    is_h1 = (max_s >= base_font_size * 1.8) and (len(line_text) < 90)
                    is_h2 = (max_s >= base_font_size * 1.25) and (len(line_text) < 90) and not line_text.endswith('.')
                    is_h3 = (
                        (has_bold and max_s >= base_font_size * 1.05 and len(line_text) < 65 and not line_text.endswith('.')) or
                        (line_text.isupper() and len(line_text) < 30 and not line_text.endswith('.'))
                    )

                    if is_bullet:
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
            elements.append(curr_el)

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

        for img_idx, img_info in enumerate(image_list):
            xref = img_info[0]
            try:
                rects = page.get_image_rects(xref)
            except Exception:
                rects = []

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
                if meaningful_label:
                    alt_text = f"Figure on Page {page_num}: {meaningful_label}"
                else:
                    alt_text = f"Figure {img_idx + 1} on Page {page_num}"

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
        # Sort lines by vertical position (top-down)
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

        is_table = len(rows) >= 2 and any(len(row) >= 2 for row in rows) and len(rows) <= 30

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

            is_bullet = bool(re.match(r"^([•\-\*\u2022\u25cf\u25cb]|\d+[\.\)]|[a-zA-Z][\.\)]|\([a-zA-Z0-9]\))\s+", txt))
            is_formula = bool(re.search(r'([=≈≠≤≥∑√∫±]|\b[a-zA-Z]\s*=\s*|\b\d+\s*[\+\-\*\/]\s*\d+)', txt)) and len(txt) < 40
            is_heading = (h >= median_h * 1.3 or (txt.isupper() and len(txt) < 40)) and len(txt) < 90 and not txt.endswith('.')

            if is_bullet:
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
        if not figures_data:
            def sort_key(el):
                bbox = el.get("bounding_box", [0, 0, 0, 0])
                top = page_h - bbox[3] if len(bbox) == 4 else 0
                left = bbox[0] if len(bbox) == 4 else 0
                col = 1 if (page_w > 400 and left >= (page_w * 0.48)) else 0
                return (col, round(top / 12) * 12, left)
            sorted_els = sorted(digital_elements, key=sort_key)
            for idx, el in enumerate(sorted_els):
                el["reading_order"] = idx + 1
            return sorted_els

        col_split = page_w * 0.48

        def get_top(el):
            bbox = el.get("bounding_box", [0, 0, 0, 0])
            return page_h - bbox[3] if len(bbox) == 4 else 0

        def get_bottom(el):
            bbox = el.get("bounding_box", [0, 0, 0, 0])
            return page_h - bbox[1] if len(bbox) == 4 else 0

        left_els = [el for el in digital_elements if el.get("bounding_box", [0, 0, 0, 0])[0] < col_split]
        right_els = [el for el in digital_elements if el.get("bounding_box", [0, 0, 0, 0])[0] >= col_split]
        is_multi_col = (page_w > 400 and len(left_els) >= 2 and len(right_els) >= 2)

        col0_els = sorted(left_els if is_multi_col else digital_elements, key=get_top)
        col1_els = sorted(right_els, key=get_top) if is_multi_col else []

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
        else:
            combined = final_col0
            for f in spanning_figs:
                combined.extend([f["figure_el"]] + f["extracted_elements"])

        for idx, el in enumerate(combined):
            el["reading_order"] = idx + 1

        return combined

