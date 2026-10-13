# GUI/renderer/mcid_writer.py

import io, base64
import fitz
from fontTools.ttLib import TTFont
from renderer.fallback_font_data import fallback_font_b64

_fitz_helv = fitz.Font("helv")

_font_bytes = base64.b64decode(fallback_font_b64)
_ttf = TTFont(io.BytesIO(_font_bytes))
_cmap = _ttf['cmap'].getBestCmap()

try:
    _space_gid = _ttf.getGlyphID(_cmap.get(32))
except Exception:
    _space_gid = 3


def escape_to_gids(s: str) -> str:
    hex_str = ''
    for char in s:
        uni = ord(char)
        glyph_name = _cmap.get(uni)
        if glyph_name:
            gid = _ttf.getGlyphID(glyph_name)
            hex_str += f"{gid:04X}"
        else:
            # a character the font lacks: a space glyph (mappable to Unicode);
            # the marked content's /ActualText still carries the real character
            hex_str += f"{_space_gid:04X}"
    return f"<{hex_str}>"

def escape_pdf_string(s: str) -> str:
    replacements = {
        '₹': 'Rs.', 
        '✓': 'ok', '→': '->', '—': '-', '–': '-',
        '’': "'", '‘': "'", '”': '"', '“': '"',
        '°': ' deg'
    }
    for k, v in replacements.items():
        s = s.replace(k, v)
    s = s.encode("cp1252", errors="ignore").decode("cp1252")
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

class MCIDWriter:
    
    FONT_RESOURCE = "helv"
    
    @staticmethod
    def _encode_actual_text(text: str) -> str:
        utf16_be = text.encode("utf-16-be")
        hex_str = "".join(f"{b:02X}" for b in utf16_be)
        return f"<FEFF{hex_str}>"
    
    @staticmethod
    def build_element_content_stream(lines_out, font_size, mcid, tag_name):
        full_text = ""
        for i, line in enumerate(lines_out):
            t = line.get("text", "")
            if i > 0 and not full_text.endswith(" ") and not full_text.endswith("-"):
                full_text += " "
            full_text += t
        hex_actual = MCIDWriter._encode_actual_text(full_text)
        
        if tag_name == "Artifact":
            bdc_instruction = "/Artifact <</Type /Pagination>> BDC"
        else:
            bdc_instruction = f"/{tag_name} <</MCID {mcid} /ActualText {hex_actual}>> BDC"
            
        parts = [
            bdc_instruction,
            "q", "BT", "3 Tr"
        ]
        prev_tz = None
        prev_tc = None
        current_font = None
        for i, line in enumerate(lines_out):
            text = line["text"]
            # Append space to help Acrobat's heuristic text extractor preserve word boundaries across lines
            if i < len(lines_out) - 1 and not text.endswith(" ") and not text.endswith("-"):
                text += " "
            try:
                text.encode("cp1252")
                font_to_use = MCIDWriter.FONT_RESOURCE
                is_unicode = False
            except UnicodeEncodeError:
                font_to_use = "FUnicode"
                is_unicode = True
                
            if current_font != font_to_use:
                parts.append(f"/{font_to_use} {font_size:.4f} Tf")
                current_font = font_to_use
                
            if line["tz"] != prev_tz:
                parts.append(f"{line['tz']:.4f} Tz")
                prev_tz = line["tz"]
            tc = line.get("tc", 0.0)
            if tc != prev_tc:
                parts.append(f"{tc:.4f} Tc")
                prev_tc = tc
            a, b, c, d, e, f = line["tm"]
            parts.append(f"{a:.4f} {b:.4f} {c:.4f} {d:.4f} {e:.4f} {f:.4f} Tm")
            
            if is_unicode:
                parts.append(f"{escape_to_gids(text)} Tj")
            else:
                parts.append(f"({escape_pdf_string(text)}) Tj")
        parts.append("ET")
        parts.append("Q")
        parts.append("EMC")
        return "\n".join(parts) + "\n"

    @staticmethod
    def build_inline_link_stream(lines_out, font, font_size, tag_name, inline_links, get_next_mcid):
        """
        Emits PDF operators for text with inline links.
        It keeps BT..ET open, but splits the Tj operations into multiple BDC..EMC blocks.
        """
        parts = ["q", "BT", "3 Tr", f"/{MCIDWriter.FONT_RESOURCE} {font_size:.4f} Tf"]
        
        mcids_used = []
        
        prev_tz = None
        prev_tc = None
        
        current_font = None

        def emit_text(chunk_str):
            nonlocal current_font
            try:
                chunk_str.encode("cp1252")
                font_to_use = MCIDWriter.FONT_RESOURCE
                is_unicode = False
            except UnicodeEncodeError:
                font_to_use = "FUnicode"
                is_unicode = True
                
            if current_font != font_to_use:
                parts.append(f"/{font_to_use} {font_size:.4f} Tf")
                current_font = font_to_use
                
            if is_unicode:
                parts.append(f"{escape_to_gids(chunk_str)} Tj")
            else:
                parts.append(f"({escape_pdf_string(chunk_str)}) Tj")

        current_mcid = None
        current_tag = tag_name
        
        def push_state(tz, tc, tm):
            nonlocal prev_tz, prev_tc
            if tz != prev_tz:
                parts.append(f"{tz:.4f} Tz")
                prev_tz = tz
            if tc != prev_tc:
                parts.append(f"{tc:.4f} Tc")
                prev_tc = tc
            a, b, c, d, e, f = tm
            parts.append(f"{a:.4f} {b:.4f} {c:.4f} {d:.4f} {e:.4f} {f:.4f} Tm")
            
        def switch_mcid(new_tag, link_info=None, chunk_text=""):
            nonlocal current_mcid, current_tag
            if current_mcid is not None:
                parts.append("EMC")
            current_mcid = get_next_mcid()
            current_tag = new_tag
            
            bdc_props = f"<</MCID {current_mcid}"
            if chunk_text:
                hex_actual = MCIDWriter._encode_actual_text(chunk_text)
                bdc_props += f" /ActualText {hex_actual}"
            bdc_props += ">>"
            
            parts.append(f"/{current_tag} {bdc_props} BDC")
            if link_info:
                annot = link_info["annot_refs"].pop(0) if link_info["annot_refs"] else None
                mcids_used.append({"mcid": current_mcid, "type": "Link", "annot_ref": annot})
            else:
                mcids_used.append({"mcid": current_mcid, "type": "Text"})
                
        for i, line in enumerate(lines_out):
            text = line["text"]
            # Append space to help Acrobat's heuristic text extractor preserve word boundaries across lines
            if i < len(lines_out) - 1 and not text.endswith(" ") and not text.endswith("-"):
                text += " "
            tz = line["tz"]
            tc = line.get("tc", 0.0)
            tm = line["tm"]
            
            source_start = line.get("source_start", 0)
            source_end = line.get("source_end", len(text))
            
            push_state(tz, tc, tm)
            
            # Find links that overlap this line
            line_links = []
            for link in inline_links:
                if link["start_offset"] < source_end and link["end_offset"] > source_start:
                    line_links.append(link)
                    
            if not line_links:
                if current_tag != tag_name or current_mcid is None:
                    switch_mcid(tag_name, chunk_text=text)
                emit_text(text)
            else:
                # Slice text by link boundaries
                line_links.sort(key=lambda x: x["start_offset"])
                cursor = 0
                for link in line_links:
                    # Map global offsets to local line offsets
                    local_start = max(0, link["start_offset"] - source_start)
                    local_end = min(len(text), link["end_offset"] - source_start)
                    
                    if local_start > cursor:
                        chunk = text[cursor:local_start]
                        if current_tag != tag_name or current_mcid is None:
                            switch_mcid(tag_name, chunk_text=chunk)
                        
                        
                        prefix_len = font.text_length(text[:cursor], fontsize=font_size)
                        x_offset = prefix_len * (tz / 100.0) + cursor * tc
                        a, b, c, d, e, f = tm
                        parts.append(f"{a:.4f} {b:.4f} {c:.4f} {d:.4f} {e + x_offset * a:.4f} {f + x_offset * b:.4f} Tm")
                        emit_text(chunk)
                        
                    if local_end > local_start:
                        chunk = text[local_start:local_end]
                        switch_mcid("Link", link, chunk_text=chunk)
                        
                        
                        prefix_len = font.text_length(text[:local_start], fontsize=font_size)
                        x_offset = prefix_len * (tz / 100.0) + local_start * tc
                        a, b, c, d, e, f = tm
                        parts.append(f"{a:.4f} {b:.4f} {c:.4f} {d:.4f} {e + x_offset * a:.4f} {f + x_offset * b:.4f} Tm")
                        emit_text(chunk)
                        
                    cursor = local_end
                    
                if cursor < len(text):
                    switch_mcid(tag_name)
                    chunk = text[cursor:]
                    
                    prefix_len = font.text_length(text[:cursor], fontsize=font_size)
                    x_offset = prefix_len * (tz / 100.0) + cursor * tc
                    a, b, c, d, e, f = tm
                    parts.append(f"{a:.4f} {b:.4f} {c:.4f} {d:.4f} {e + x_offset * a:.4f} {f + x_offset * b:.4f} Tm")
                    emit_text(chunk)
                    
        parts.append("EMC")
        parts.append("ET")
        parts.append("Q")
        
        return "\n".join(parts) + "\n", mcids_used


    @staticmethod
    def build_invisible_rect_stream(geom, mcid, tag_name, crop_offset=(0, 0)):
        left, bottom, width, height = geom.get("left", 0), geom.get("bottom", 0), geom.get("width", 0), geom.get("height", 0)
        
        if tag_name == "Artifact":
            bdc_instruction = "/Artifact <</Type /Pagination>> BDC"
        else:
            bdc_instruction = f"/{tag_name} <</MCID {mcid}>> BDC"
            
        cx, cy = crop_offset
        left += cx
        bottom += cy
            
        parts = [
            bdc_instruction,
            "q",
            f"{left:.4f} {bottom:.4f} {width:.4f} {height:.4f} re",
            "n", 
            "Q",
            "EMC"
        ]
        return "\n".join(parts) + "\n"
    @staticmethod
    def build_native_font_stream(font_runs, mcid, tag_name, crop_offset=(0, 0)):
        """Raw operators for multiple font runs, preserving original glyphs and coordinates."""
        full_text = "".join(run.get("text", "") for run in font_runs if run.get("text"))
        hex_actual = MCIDWriter._encode_actual_text(full_text)
        
        if tag_name == "Artifact":
            bdc_instruction = "/Artifact <</Type /Pagination>> BDC"
        else:
            bdc_instruction = f"/{tag_name} <</MCID {mcid} /ActualText {hex_actual}>> BDC"
            
        parts = [
            bdc_instruction,
            "q", "BT", "3 Tr"
        ]
        
        cx, cy = crop_offset
        
        for run in font_runs:
            font = run.get("font", "")
            text = run.get("text", "")
            if not text:
                continue
            
            is_zapf = font == "ZapfDingbats"
            is_bad_helv = "Helvetica" in font and text == "\u2022"
            
            # If it's a known invalid bullet or checkbox, its background glyph was redacted. 
            # We must emit it visibly with the fallback font.
            is_invalid_bullet = ((is_zapf and text == "I") or is_bad_helv) and tag_name == "Lbl"
            
            # For invisible accessible text, always use standard Helvetica / FUnicode.
            # Document subset fonts (e.g. /T1_23) lack standard ASCII mappings, truncate text,
            # and cause Acrobat to display empty tags and collapsed bounding boxes.
            res = f"/{MCIDWriter.FONT_RESOURCE}"
            is_unicode = False
            
            if is_invalid_bullet:
                parts.append("0 Tr")
                res = "/fallback"
            else:
                try:
                    text.encode("cp1252")
                except UnicodeEncodeError:
                    res = "/FUnicode"
                    is_unicode = True
                    
            size = run.get("size", 11.0)
            parts.append(f"{res} {size:.4f} Tf")
            
            # Exact horizontal scaling (Tz) matching original visual bbox width
            bbox = run.get("bbox")
            applied_tz = False
            if bbox and len(bbox) == 4 and not is_unicode and not is_invalid_bullet:
                target_w = float(bbox[2]) - float(bbox[0])
                try:
                    helv_w = _fitz_helv.text_length(text, fontsize=size)
                    if helv_w > 0 and target_w > 0:
                        tz = 100.0 * target_w / helv_w
                        tz = max(50.0, min(200.0, tz))
                        parts.append(f"{tz:.2f} Tz")
                        applied_tz = True
                except Exception:
                    pass
            
            origin = run.get("origin")
            if not origin:
                origin = (bbox[0], bbox[1]) if bbox else (0.0, 0.0)
                
            ox, oy = origin
            ox += cx
            oy += cy
            
            parts.append(f"1.0000 0.0000 0.0000 1.0000 {ox:.4f} {oy:.4f} Tm")
            
            if is_invalid_bullet:
                parts.append(f"<95> Tj")
            elif is_unicode:
                parts.append(f"{escape_to_gids(text)} Tj")
            else:
                parts.append(f"({escape_pdf_string(text)}) Tj")
                
            if applied_tz:
                parts.append("100.00 Tz")
            
            if is_invalid_bullet:
                # Restore invisible mode for any subsequent normal font runs
                parts.append("3 Tr")
            
        parts.append("ET")
        parts.append("Q")
        parts.append("EMC")
        return "\n".join(parts) + "\n"

    @staticmethod
    def build_native_inline_link_stream(font_runs, tag_name, inline_links, get_mcid_fn, crop_offset=(0, 0)):
        mcids_used = []
        annot_rects = {}
        
        current_mcid = get_mcid_fn()
        current_tag = tag_name
        parts = []
        
        cx, cy = crop_offset
        
        def switch_mcid(new_tag, link_dict=None):
            nonlocal current_mcid, current_tag
            if parts and parts[-1] != "EMC":
                parts.append("EMC")
                
            current_mcid = get_mcid_fn()
            current_tag = new_tag
            
            if new_tag == "Link" and link_dict:
                parts.append(f"/Link <</MCID {current_mcid}>> BDC")
                mcids_used.append({"mcid": current_mcid, "type": "Link", "target_url": link_dict["target_url"], "rect": link_dict.get("rects", [])})
            else:
                parts.append(f"/{new_tag} <</MCID {current_mcid}>> BDC")
                mcids_used.append({"mcid": current_mcid, "type": "Text"})

        parts.append(f"/{tag_name} <</MCID {current_mcid}>> BDC")
        mcids_used.append({"mcid": current_mcid, "type": "Text"})
        
        parts.append("q")
        parts.append("BT")
        parts.append("3 Tr")
        
        
        
        for run in font_runs:
            font = run.get("font", "")
            text = run.get("text", "")
            
            clean_text = text.replace(" ", "").replace("\n", "")
            matched_link = None
            for link in inline_links:
                clean_link_text = link.get("text", "").replace(" ", "").replace("\n", "")
                if clean_link_text and clean_link_text in clean_text:
                    matched_link = link
                    break
                    
            if matched_link:
                if matched_link["text"] not in annot_rects:
                    annot_rects[matched_link["text"]] = []
                bbox = run.get("bbox", [0, 0, 0, 0])
                bbox = [bbox[0] + cx, bbox[1] + cy, bbox[2] + cx, bbox[3] + cy]
                annot_rects[matched_link["text"]].append(bbox)
                
                switch_mcid("Link", matched_link)
            else:
                if current_tag == "Link":
                    switch_mcid(tag_name)
                    
            is_zapf = font == "ZapfDingbats"
            is_bad_helv = "Helvetica" in font and text == "\u2022"
            is_invalid_bullet = (is_zapf and text == "I") or is_bad_helv
            
            res = run.get("resource", f"/{MCIDWriter.FONT_RESOURCE}")
            if not res.startswith("/"):
                res = "/" + res
                
            if is_invalid_bullet:
                parts.append("0 Tr")
                res = "/fallback"
                
            is_unicode = False
            if res == f"/{MCIDWriter.FONT_RESOURCE}" and not is_invalid_bullet:
                try:
                    text.encode("cp1252")
                except UnicodeEncodeError:
                    res = "/FUnicode"
                    is_unicode = True
                
            size = run.get("size", 11.0)
            parts.append(f"{res} {size:.4f} Tf")
            
            origin = run.get("origin")
            if not origin:
                bbox = run.get("bbox", [0, 0, 0, 0])
                origin = (bbox[0], bbox[1])
                
            ox, oy = origin
            ox += cx
            oy += cy
            
            parts.append(f"1.0000 0.0000 0.0000 1.0000 {ox:.4f} {oy:.4f} Tm")

            
            if is_invalid_bullet:
                parts.append(f"<95> Tj")
            elif is_unicode:
                parts.append(f"{escape_to_gids(text)} Tj")
            else:
                parts.append(f"({escape_pdf_string(text)}) Tj")
            
            if is_invalid_bullet:
                parts.append("3 Tr")
            
        parts.append("ET")
        parts.append("Q")
        parts.append("EMC")
        
        return "\n".join(parts) + "\n", mcids_used, annot_rects
