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
    def _join_line_spans(spans):
        """
        Joins spans on a single line, inserting a space between spans where layout,
        bounding boxes, or word boundaries indicate a missing separator.
        Preserves legitimate hyphenation and avoids spaces in URLs, numbers, etc.
        """
        if not spans:
            return ""
        if len(spans) == 1:
            return spans[0].get("text", "")
        
        parts = []
        prev_s = None
        for s in spans:
            txt = s.get("text", "")
            if not txt:
                continue
            if prev_s is None:
                parts.append(txt)
                prev_s = s
                continue
            
            prev_txt = parts[-1]
            if prev_txt.endswith(" ") or txt.startswith(" "):
                parts.append(txt)
                prev_s = s
                continue
            
            prev_box = prev_s.get("bbox", [0, 0, 0, 0])
            curr_box = s.get("bbox", [0, 0, 0, 0])
            gap = curr_box[0] - prev_box[2]
            min_fs = min(prev_s.get("size", 10.0), s.get("size", 10.0))
            
            # Preserve hyphenation
            if prev_txt.rstrip().endswith(('-', '\u2010', '\u2011')) and txt.lstrip() and txt.lstrip()[0].isalpha():
                parts.append(txt)
                prev_s = s
                continue
                
            # URL / email protection
            joined_token = (prev_txt.split()[-1] if prev_txt.split() else "") + (txt.split()[0] if txt.split() else "")
            if "@" in joined_token or joined_token.startswith(("http://", "https://", "www.")):
                parts.append(txt)
                prev_s = s
                continue
                
            prev_r = prev_txt.rstrip()
            curr_l = txt.lstrip()
            if not prev_r or not curr_l:
                parts.append(txt)
                prev_s = s
                continue

            # Number with decimal or comma
            if prev_r[-1].isdigit() and (curr_l.startswith(('.', ',')) or (prev_r.endswith(('.', ',')) and curr_l[0].isdigit())):
                parts.append(txt)
                prev_s = s
                continue

            prev_last_c = prev_r[-1]
            curr_first_c = curr_l[0]
            
            # Punctuation separator (e.g., 'Ask:' and 'What' in separate spans)
            is_punct_boundary = (
                (prev_last_c in (':', ';', '!', '?') and curr_first_c.isalnum()) or
                (prev_last_c in (')', ']', '}', '"', '”') and curr_first_c.isalnum()) or
                (prev_last_c.isalnum() and curr_first_c in ('(', '[', '{', '"', '“'))
            )
            
            # Word space in typography is ~0.25-0.35 * fontsize (e.g. 2-3.5pt).
            # Kerning inside a word is < 0.15 * fontsize (< 1.2pt).
            is_word_space = gap >= max(1.5, 0.20 * min_fs)
            is_punct_space = is_punct_boundary and gap >= 0.5
            is_token_boundary = (
                (prev_last_c.isalpha() and curr_first_c.isdigit() and len(prev_txt.strip()) >= 2) or
                (prev_last_c.isdigit() and curr_first_c.isalpha()) or
                (curr_box[1] > prev_box[1] + 2)
            )
            
            if is_word_space or is_punct_space or is_token_boundary:
                parts.append(" " + txt)
            else:
                parts.append(txt)
            prev_s = s
            
        return "".join(parts)

    @staticmethod
    def _math_to_speech(txt: str) -> str:
        s = txt.strip()
        # Common radicals and powers
        s = s.replace('√', 'the square root of ')
        s = s.replace('²', ' squared')
        s = s.replace('³', ' cubed')
        s = s.replace('^2', ' squared')
        s = s.replace('^3', ' cubed')
        s = s.replace('−', ' minus ')
        s = s.replace('+', ' plus ')
        s = s.replace('=', ' equals ')
        s = s.replace('±', ' plus or minus ')
        s = s.replace('≠', ' does not equal ')
        s = s.replace('≤', ' is less than or equal to ')
        s = s.replace('≥', ' is greater than or equal to ')
        s = s.replace('≈', ' is approximately equal to ')
        s = re.sub(r'\bx_1\b|\bx\s*1\b', 'x sub one', s)
        s = re.sub(r'\bx_2\b|\bx\s*2\b', 'x sub two', s)
        s = re.sub(r'\by_1\b|\by\s*1\b', 'y sub one', s)
        s = re.sub(r'\by_2\b|\by\s*2\b', 'y sub two', s)
        s = re.sub(r'\(([^)]+)\)\s*(?:2|²|\^2)', r'the quantity \1 squared', s)
        s = re.sub(r'\(([^)]+)\)\s*(?:3|³|\^3)', r'the quantity \1 cubed', s)
        s = re.sub(r'\b([a-zA-Z])\s*2\b', r'\1 squared', s)
        s = re.sub(r'\b([a-zA-Z])\s*3\b', r'\1 cubed', s)
        s = re.sub(r'\bminus\s*([0-9]+)\b', r'negative \1', s)
        num_map = {'0': 'zero', '1': 'one', '2': 'two', '3': 'three', '4': 'four',
                   '5': 'five', '6': 'six', '7': 'seven', '8': 'eight', '9': 'nine', '10': 'ten'}
        for k, v in num_map.items():
            s = re.sub(r'\b' + k + r'\b', v, s)
        s = re.sub(r'\s+', ' ', s).strip()
        if s and not s.endswith('.'):
            s += '.'
        return s

    @staticmethod
    def _is_math_formula(txt: str) -> bool:
        t = txt.strip()
        if not t:
            return False
        if t.endswith('?'):
            return False
            
        words = t.split()
        prose_words = [w.lower().rstrip(':,;.') for w in words if len(w) >= 3 and w.isalpha()]
        
        COMMON_PROSE = {
            'the', 'and', 'that', 'this', 'from', 'with', 'between', 'which', 'each', 'under',
            'where', 'given', 'find', 'solve', 'graph', 'plot', 'table', 'write', 'read',
            'check', 'exercise', 'example', 'equation', 'called', 'general', 'standard',
            'circle', 'center', 'radius', 'form', 'linear', 'point', 'points', 'intercept',
            'intercepts', 'distance', 'midpoint', 'plane', 'system', 'value', 'values',
            'following', 'defined', 'written', 'substitute', 'rectangular', 'coordinate'
        }
        stop_count = sum(1 for w in prose_words if w in COMMON_PROSE)
        if stop_count >= 2 or len(prose_words) >= 4:
            return False

        has_math_op = bool(re.search(r'[=≈≠≤≥∑√∫±∓∂∆πθαβγλμσ∞²³⁴\^]|[\+\−\-\*\/·]\s*(?:[0-9a-zA-Z\(]|√)|(?:[0-9a-zA-Z\)]\s*[\+\−\-\*\/·])', t))
        has_eq = '=' in t or '≈' in t or '≠' in t or '≤' in t or '≥' in t
        is_assignment = bool(re.match(r'^(?:[a-zA-Z]|[A-Za-z]\([a-zA-Z0-9,\s]+\))\s*=\s*.+', t))
        is_poly = bool(re.search(r'\b[0-9]?[a-zA-Z]\s*(?:\^|\²|\³|\d)?\s*[\+\−\-].+=\s*[0-9a-zA-Z]', t))
        is_poly_eq = bool(re.search(r'[0-9a-zA-Z\)\]]\s*=\s*0\b|\b0\s*=\s*[0-9a-zA-Z]', t))
        is_coord = bool(re.match(r'^\(\s*-?[a-zA-Z0-9]+(?:\/[a-zA-Z0-9]+)?\s*,\s*-?[a-zA-Z0-9]+(?:\/[a-zA-Z0-9]+)?\s*\)$', t))
        is_underlines = bool(re.match(r'^[_\s—–\-]+$', t)) and len(t) >= 3
        is_root_expr = ('√' in t or '²' in t or '³' in t or '^2' in t or '^3' in t)
        is_alg = bool(re.search(r'\([a-zA-Z0-9\s\+\−\-]+\)\s*(?:\d|\^|\²|\³)', t))

        return (has_math_op and (has_eq or is_poly or is_root_expr or is_assignment or is_alg)) or is_coord or is_poly_eq or is_underlines

    @staticmethod
    def _detect_vector_graphs(page, table_regions=None):
        """
        Detects coordinate axes, function graphs, geometric plots, and charts made of vector drawings.
        Returns a list of fitz.Rect for valid graphs.
        """
        pw, ph = page.rect.width, page.rect.height
        candidates = []
        try:
            drawings = page.get_drawings()
            for d in drawings:
                r = fitz.Rect(d['rect'])
                if (r.width > pw * 0.70 and r.height > 100) or r.width >= pw * 0.90:
                    continue
                if r.width < 10 or r.height < 10:
                    continue
                if (r.height <= 3.5 and r.width > 0.4 * pw) or (r.width <= 3.5 and r.height > 0.4 * ph):
                    continue
                if table_regions:
                    if any(tr[0] - 2 <= r.x0 and r.x1 <= tr[2] + 2 and tr[1] - 2 <= r.y0 and r.y1 <= tr[3] + 2 for tr in table_regions):
                        continue
                items = d.get('items', [])
                has_curves = any(it[0] == 'c' for it in items)
                has_grid = len(items) >= 6 or (len(items) >= 2 and abs(r.width - r.height) < 45 and r.width >= 25)
                if has_curves or has_grid:
                    candidates.append(r)
        except Exception:
            return []

        if not candidates:
            return []

        clusters = []
        for cr in candidates:
            merged = False
            exp = fitz.Rect(cr.x0 - 10, cr.y0 - 10, cr.x1 + 10, cr.y1 + 10)
            for i, c in enumerate(clusters):
                if c['rect'].intersects(exp):
                    clusters[i]['rect'] = clusters[i]['rect'] | cr
                    clusters[i]['count'] += 1
                    merged = True
                    break
            if not merged:
                clusters.append({'rect': cr, 'count': 1})

        valid_graphs = []
        for c in clusters:
            r = c['rect']
            if 30 <= r.width <= pw * 0.75 and 30 <= r.height <= ph * 0.75 and c['count'] >= 2:
                expanded_r = fitz.Rect(max(0, r.x0 - 4), max(0, r.y0 - 4), min(pw, r.x1 + 4), min(ph, r.y1 + 4))
                valid_graphs.append(expanded_r)
        return valid_graphs


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
                        len(multi_col_rows) >= 2 and
                        fill_ratio >= 0.35 and
                        len(cells_with_data) >= 4 and
                        (tab_w >= 80) and
                        (tab_h >= 20) and
                        not has_sec and
                        not has_callout and
                        not has_long_prose and
                        not has_calc_header and
                        not is_irregular_grid and
                        not is_sparse_ticks and
                        not is_plot_grid and
                        not is_tall_layout and
                        avg_words <= 7.0
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

        QUESTION_WORDS = {
            "who", "what", "when", "where", "why", "how", "which", "whose", "whom",
            "do", "does", "did", "is", "are", "was", "were", "can", "could", "would",
            "should", "may", "might", "will"
        }
        INSTRUCTION_WORDS = {
            "read", "write", "complete", "answer", "choose", "select", "fill", "find",
            "solve", "model", "ask", "tell", "have", "use", "show", "check", "look",
            "think", "guide", "explain", "discuss", "describe", "identify", "compare",
            "contrast", "list", "define", "match", "circle", "underline", "draw",
            "work", "practice", "note", "remember", "follow", "provide", "give", "state"
        }
        COMMON_SECTION_HEADINGS = {
            "introduction", "overview", "summary", "background", "methodology", "methods",
            "materials", "results", "discussion", "conclusion", "conclusions", "references",
            "bibliography", "appendix", "index", "glossary", "preface", "foreword"
        }

        # Collect Figure/Graph regions (both vector graphs and raster images) to prevent extracting text from inside graphs
        figure_regions = list(PaddleLayoutExtractor._detect_vector_graphs(page, table_regions))
        try:
            for img_info in page.get_images():
                for r_img in page.get_image_rects(img_info[0]):
                    if r_img.width >= 20 and r_img.height >= 20 and not (r_img.width > page_w * 0.95 and r_img.height > page_h * 0.95):
                        figure_regions.append(r_img)
        except Exception:
            pass

        # Expand figure regions by 8pt to completely engulf axis numbers, tick marks, and point labels
        expanded_figure_regions = [
            fitz.Rect(max(0, fr.x0 - 8), max(0, fr.y0 - 8), min(page_w, fr.x1 + 8), min(page_h, fr.y1 + 8))
            for fr in figure_regions if fr.width > 20 and fr.height > 20
        ]

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

                # Check if block falls inside any detected figure / graph (graphs must be single Figure tag, no extracted text)
                b_rect = fitz.Rect(b_box)
                is_in_figure = any(
                    fr.intersects(b_rect) and
                    (min(fr.x1, b_box[2]) - max(fr.x0, b_box[0])) * (min(fr.y1, b_box[3]) - max(fr.y0, b_box[1])) >= 0.15 * max((b_box[2] - b_box[0]) * (b_box[3] - b_box[1]), 1.0)
                    for fr in expanded_figure_regions
                )
                if is_in_figure:
                    continue


                if curr_el:
                    elements.append(curr_el)
                    curr_el = None

                for line in b.get("lines", []):
                    # Check individual line against figure regions (to filter axis numbers/labels)
                    l_rect = fitz.Rect(line.get("bbox", [0, 0, 0, 0]))
                    if any(fr.intersects(l_rect) and (min(fr.x1, l_rect.x1) - max(fr.x0, l_rect.x0)) * (min(fr.y1, l_rect.y1) - max(fr.y0, l_rect.y0)) >= 0.15 * max(l_rect.width * l_rect.height, 1.0) for fr in expanded_figure_regions):
                        continue

                    spans = line.get("spans", [])
                    line_text = PaddleLayoutExtractor._join_line_spans(spans).strip()
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
                        s.get("flags", 0) & 2 or any(k in s.get("font", "").lower() for k in ("bold", "black", "semibold", "semi-bold", "demi", "heavy", "medium"))
                        for s in spans
                    )

                    l_box = list(line.get("bbox", [0, 0, 0, 0]))
                    pdf_bbox = [
                        round(l_box[0], 2), round(page_h - l_box[3], 2),
                        round(l_box[2], 2), round(page_h - l_box[1], 2)
                    ]

                    # Check for hierarchical outline section numbering:
                    # e.g., "2", "2.", "2.1", "2.2", "2.2.1", "2.2.1.2", "2.3.1.2"
                    # Rule: Heading levels follow document outline rather than visual appearance alone.
                    outline_match = re.match(r'^\s*(?:(?:Chapter|Section|Part|Appendix)\s+)?(\d+(?:\.\d+)*)\.?\s*(\S.*)?$', line_text, re.IGNORECASE)
                    is_outline_heading = False
                    outline_h_level = None

                    if outline_match:
                        num_str = outline_match.group(1)
                        parts = num_str.split('.')
                        title_part = (outline_match.group(2) or "").strip()

                        if len(parts) >= 2:
                            # Multi-level numbered section heading (2.1, 2.2, 2.2.1, 2.2.1.2, etc.)
                            if len(line_text) < 120 and not any(ch in line_text for ch in ('=', '\u2212', '\u221a', '______', '+')) and (has_bold or not title_part.endswith('.') or max_s >= base_font_size * 1.0):
                                is_outline_heading = True
                                outline_h_level = min(6, len(parts))
                        elif len(parts) == 1:
                            # Single-number section e.g. "Chapter 2" or "1. Introduction"
                            has_ch_prefix = bool(re.match(r'^\s*(?:Chapter|Section|Part|Appendix)\s+', line_text, re.IGNORECASE))
                            title_words = title_part.split()
                            w0 = title_words[0].lower().rstrip(':,;.?!') if title_words else ""
                            is_math = any(ch in line_text for ch in ('=', '\u2212', '\u221a', '______', '+', '(', ')'))
                            is_question_or_instruction = (
                                is_math or
                                not (w0 and w0[0].isalpha()) or
                                w0 in QUESTION_WORDS or
                                w0 in INSTRUCTION_WORDS or
                                title_part.endswith('?') or
                                line_text.endswith('?') or
                                w0 in ("a", "an", "the", "in", "on", "at", "to", "for", "with", "by", "from", "if", "then", "as", "that", "this") or
                                any(p in title_part for p in (',', ';', ':', '?', '!')) or
                                title_part.endswith('.')
                            )
                            is_common = w0 in COMMON_SECTION_HEADINGS or title_part.lower() in COMMON_SECTION_HEADINGS
                            is_short_title = bool(title_part) and len(title_words) <= 5 and len(line_text) < 45 and not is_question_or_instruction
                            is_title_candidate = not is_question_or_instruction and (
                                has_ch_prefix or
                                is_common or
                                (line_text.isupper() and len(line_text) < 35 and any(c.isalpha() for c in line_text)) or
                                (is_short_title and (has_bold or max_s >= base_font_size * 1.15))
                            )
                            if is_title_candidate:
                                is_outline_heading = True
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

                    # Exclude math formulas, single variables, or equation fragments from becoming headings
                    is_math_or_var = (
                        len(line_text) <= 1 or
                        any(ch in line_text for ch in ('=', '\u2212', '\u221a', '______', '+', '(')) or
                        (len(line_text.split()) == 1 and line_text.lower().rstrip(':,;.?!') not in COMMON_SECTION_HEADINGS and line_text.lower().rstrip(':,;.?!') not in {"examples", "summary", "exercises", "review", "concepts", "objectives", "definitions", "notes", "references", "index", "glossary", "background", "conclusion", "conclusions", "appendix", "part"} and not re.match(r'^(?:chapter|section|part|unit)\s*\d*$', line_text, re.I))
                    )

                    # Subheading callouts like KEY CONCEPTS, EXAMPLES under a section are H3
                    is_sub_callout = line_text.strip().upper() in ("KEY CONCEPTS", "EXAMPLES", "CONCEPTS", "DEFINITIONS", "SUMMARY", "IMPORTANT POINTS")

                    # Heading classification based on typography hierarchy (for non-numbered headings)
                    is_h1 = (max_s >= base_font_size * 1.8) and (len(line_text) < 90) and (not has_seen_h1) and (not is_math_or_var)
                    is_h2 = not is_math_or_var and not is_sub_callout and (
                        (max_s >= base_font_size * 1.25 and len(line_text) < 90 and not line_text.endswith('.')) or
                        (line_text.isupper() and len(line_text) < 35 and any(c.isalpha() for c in line_text) and not line_text.endswith('.'))
                    )
                    is_page_number = bool(re.match(r'^[A-Za-z]?\d{1,5}$', line_text.strip()))
                    is_h3 = not is_page_number and not is_math_or_var and (
                        is_sub_callout or
                        (has_bold and max_s >= base_font_size * 0.95 and len(line_text) < 65 and 1 <= len(line_text.split()) <= 7 and not line_text.endswith(('.', ':', ';', ',', '!', '?'))) or
                        (line_text.isupper() and len(line_text) < 30 and not line_text.endswith('.') and any(c.isalpha() for c in line_text))
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

                    elif PaddleLayoutExtractor._is_math_formula(line_text):
                        if curr_el:
                            elements.append(curr_el)
                            curr_el = None
                        alt_math = PaddleLayoutExtractor._math_to_speech(line_text)
                        elements.append({
                            "id": id_counter,
                            "type": "formula",
                            "tag": "Formula",
                            "content": line_text,
                            "alt_text": alt_math,
                            "bounding_box": pdf_bbox,
                            "font_size": round(max_s, 1),
                            "confidence": 1.0,
                            "_spans_meta": spans
                        })
                        id_counter += 1
                        continue

                    else:
                        # Continuation or Paragraph
                        if curr_el and curr_el["type"] == "list_item":
                            # A line that is bold, short, and title-like is NEVER a continuation of a list item
                            is_bold_title = has_bold and len(line_text) < 65 and 1 <= len(line_text.split()) <= 7 and not line_text.endswith('.')
                            if is_bold_title:
                                elements.append(curr_el)
                                curr_el = {
                                    "id": id_counter,
                                    "type": "heading",
                                    "tag": "H2",
                                    "heading_level": 2,
                                    "content": line_text,
                                    "bounding_box": pdf_bbox,
                                    "font_size": round(max_s, 1),
                                    "is_bold": True,
                                    "confidence": 1.0
                                }
                                id_counter += 1
                                continue

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
                        elif curr_el and curr_el["type"] == "paragraph":
                            # Merge regular paragraph lines
                            if curr_el["content"].rstrip().endswith(('-', '\u2010', '\u2011')) and line_text and line_text[0].isalpha():
                                curr_el["content"] = curr_el["content"].rstrip() + line_text.lstrip()
                            else:
                                curr_el["content"] = curr_el["content"].rstrip() + " " + line_text.lstrip()
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

        # 6. Mathematical Formula Classification & Cleanup
        # Convert any paragraph whose content is a genuine math formula into a dedicated Formula tag
        eq_elements = []
        for el in elements:
            tag = el.get("tag", "")
            c = (el.get("content") or "").strip()
            if tag == "P" and PaddleLayoutExtractor._is_math_formula(c):
                el["type"] = "formula"
                el["tag"] = "Formula"
                el["alt_text"] = PaddleLayoutExtractor._math_to_speech(c)
            # Filter out standalone underline/separator artifacts left over from fraction/radical bars
            if re.match(r'^[_\s—–\-]+$', c) and len(c) >= 2:
                continue
            eq_elements.append(el)
        elements = eq_elements


        # 7. Merge Unit/Chapter prefix with following unit title
        # e.g., "UNIT 7" + "The Past" -> single <H1> "UNIT 7 The Past"
        merged_unit_elements = []
        skip_idx = -1
        for i, el in enumerate(elements):
            if i == skip_idx:
                continue
            txt = (el.get("content") or el.get("text") or "").strip()
            m_unit = re.match(r'^(UNIT|CHAPTER|PART|MODULE|LESSON|SECTION)\s*(\d+)$', txt, re.IGNORECASE)
            if m_unit and i + 1 < len(elements):
                next_el = elements[i + 1]
                next_txt = (next_el.get("content") or next_el.get("text") or "").strip()
                if len(next_txt) < 80 and not next_txt.endswith('.') and not re.match(r'^(?:UNIT|CHAPTER|PART)\b', next_txt, re.I):
                    prefix = f"{m_unit.group(1).upper()} {m_unit.group(2)}"
                    el["content"] = f"{prefix} {next_txt}"
                    b0 = el.get("bounding_box", [0, 0, 0, 0])
                    b1 = next_el.get("bounding_box", [0, 0, 0, 0])
                    el["bounding_box"] = [min(b0[0], b1[0]), min(b0[1], b1[1]), max(b0[2], b1[2]), max(b0[3], b1[3])]
                    el["type"] = "heading"
                    el["tag"] = "H1"
                    el["heading_level"] = 1
                    has_seen_h1 = True
                    skip_idx = i + 1
            merged_unit_elements.append(el)
        elements = merged_unit_elements

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
        fig_items = []

        # 1. Raster images
        for img_idx, img_info in enumerate(image_list):
            xref = img_info[0]
            try:
                rects = page.get_image_rects(xref)
            except Exception:
                rects = []

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
                w, h = r.width, r.height
                if w < 20 or h < 20:
                    continue
                if w > page_w * 0.95 and h > page_h * 0.95:
                    continue
                fig_items.append({"rect": r, "xref": xref, "is_vector": False})

        # 2. Vector graphs (coordinate grids, curve plots, geometric figures)
        vector_graphs = PaddleLayoutExtractor._detect_vector_graphs(page)
        for gr in vector_graphs:
            if not any(gr.intersects(it["rect"]) for it in fig_items):
                fig_items.append({"rect": gr, "xref": 0, "is_vector": True})

        for item in fig_items:
            r = item["rect"]
            xref = item["xref"]
            is_vector = item["is_vector"]

            # Expand vector graphs by 6pt to cleanly enclose axes, ticks, and labels as 1 unified Figure
            exp_r = fitz.Rect(max(0, r.x0 - 6), max(0, r.y0 - 6), min(page_w, r.x1 + 6), min(page_h, r.y1 + 6)) if is_vector else r

            pdf_bbox = [
                round(exp_r.x0, 2),
                round(page_h - exp_r.y1, 2),
                round(exp_r.x1, 2),
                round(page_h - exp_r.y0, 2)
            ]

            fig_counter += 1
            default_alt = f"Figure {fig_counter} on Page {page_num}: Mathematical Graph" if is_vector else f"Figure {fig_counter} on Page {page_num}"
            alt_text = default_alt

            # Accessible description via Gemini if enabled
            try:
                from core.gemini_service import GeminiService
                if GeminiService.is_enabled():
                    img_bytes = None
                    if page is not None and exp_r and exp_r.width > 10 and exp_r.height > 10:
                        crop_pix = page.get_pixmap(clip=exp_r, dpi=150)
                        img_bytes = crop_pix.tobytes("png")
                    elif xref > 0 and doc is not None:
                        img_pix = fitz.Pixmap(doc, xref)
                        if img_pix.n >= 5:
                            img_pix = fitz.Pixmap(fitz.csRGB, img_pix)
                        img_bytes = img_pix.tobytes("png")
                    if img_bytes:
                        hint = "Mathematical Graph" if is_vector else ""
                        g_alt = GeminiService.generate_figure_alt_text(img_bytes, context_hint=hint)
                        if g_alt:
                            alt_text = g_alt
            except Exception as g_err:
                pass

            # Suppress any text elements inside this graph/figure from existing_elements
            if existing_elements:
                f_rect = fitz.Rect(round(exp_r.x0, 2), round(exp_r.y0, 2), round(exp_r.x1, 2), round(exp_r.y1, 2))
                clean_existing = []
                for el in existing_elements:
                    b = el.get("bounding_box", [0, 0, 0, 0])
                    el_rect = fitz.Rect(b[0], page_h - b[3], b[2], page_h - b[1])
                    if f_rect.intersects(el_rect):
                        inter = f_rect & el_rect
                        if inter.width * inter.height >= 0.15 * max(el_rect.width * el_rect.height, 1.0):
                            continue
                    clean_existing.append(el)
                existing_elements[:] = clean_existing

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

            figures_data.append({
                "figure_el": fig_el,
                "extracted_elements": [],  # Graphs are single Figure tags; no sub-elements!
                "bbox": pdf_bbox,
                "top_y": round(exp_r.y0, 2),
                "bottom_y": round(exp_r.y1, 2),
                "left_x": round(exp_r.x0, 2),
                "right_x": round(exp_r.x1, 2)
            })
            flat_elements.append(fig_el)

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

            col0_figs.sort(key=lambda f: f["top_y"])
            col1_figs.sort(key=lambda f: f["top_y"])
            spanning_figs.sort(key=lambda f: f["top_y"])

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

