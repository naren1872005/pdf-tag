import json
import uuid
from typing import List, Dict, Any, Tuple
from dataclasses import dataclass, asdict

def calculate_iou(boxA, boxB):
    # Box format: [x0, y0, x1, y1]
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])

    interArea = max(0, xB - xA) * max(0, yB - yA)
    if interArea == 0:
        return 0.0

    boxAArea = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
    boxBArea = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])

    iou = interArea / float(boxAArea + boxBArea - interArea)
    return iou

@dataclass
class NativeTextStyle:
    font_name: str
    font_resource: str
    subset_name: str
    font_type: str
    font_size: float
    encoding: str
    glyph_support: bool
    font_flags: int
    color: int
    writing_mode: int
    render_mode: int
    baseline: float
    origin: tuple
    text_matrix: tuple
    character_spacing: float
    word_spacing: float

class FontMapper:
    """
    Maps semantic nodes to native PDF spans at the glyph level.
    Outputs native_font_map.json and font_mapping_report.json.
    """
    
    @staticmethod
    def map_nodes(nodes: List[Any], extracted_data: Dict[str, Any], resolver) -> Tuple[List[Dict], List[Dict]]:

        font_map = []
        report = []
        
        page_height = extracted_data.get("page_height", 792.0)
        
        # Flatten native spans/characters from PyMuPDF
        native_chars = []
        for block in extracted_data.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    # We extract character level info for precision
                    for char in span.get("chars", []):
                        x0, y0, x1, y1 = char.get("bbox", [0, 0, 0, 0])
                        crop_x0 = extracted_data.get("crop_x0", 0.0)
                        crop_y0 = extracted_data.get("crop_y0", 0.0)
                        
                        # PyMuPDF get_text returns Top-Left MediaBox-global coordinates.
                        # We must convert them to CropBox-local coordinates so they match Gemini's format.
                        cx0, cy0 = x0 - crop_x0, y0 - crop_y0
                        cx1, cy1 = x1 - crop_x0, y1 - crop_y0
                        
                        # Convert CropBox-local top-left to CropBox-local bottom-left
                        new_y0 = page_height - cy1
                        new_y1 = page_height - cy0
                        conv_bbox = [cx0, new_y0, cx1, new_y1]
                        
                        ox, oy = char.get("origin", (0, 0))
                        cox, coy = ox - crop_x0, oy - crop_y0
                        conv_origin = (cox, page_height - coy)
                        
                        char_data = {
                            "id": len(native_chars),
                            "text": char.get("c", ""),
                            "bbox": conv_bbox,
                            "font": span.get("font", ""),
                            "size": span.get("size", 0),
                            "flags": span.get("flags", 0),
                            "color": span.get("color", 0),
                            "origin": conv_origin
                        }
                        native_chars.append(char_data)
        
        used_chars = set()
        
        # Sort nodes by reading order to consume characters sequentially
        def get_ro(n):
            meta = getattr(n, "metadata", {}) if hasattr(n, "metadata") else n.get("metadata", {})
            return meta.get("reading_order", 999999)
            
        nodes = sorted(nodes, key=get_ro)
        
        for node in nodes:
            sem_text = getattr(node, "text", "")
            if not sem_text and hasattr(node, "semantic"):
                sem_text = node.semantic.get("content", "") or node.semantic.get("actual_text", "")
            if not sem_text and isinstance(node, dict):
                sem_text = node.get("text", "") or node.get("content", "")
                
            geom = getattr(node, "geometry", {}) if hasattr(node, "geometry") else node.get("geometry", {})
            node_type = getattr(node, "semantic", {}).get("type") if hasattr(node, "semantic") else node.get("semantic", {}).get("type")
            if node_type == "list_label":
                sem_bbox = geom.get("bbox") or geom.get("parent_bbox")
            else:
                sem_bbox = geom.get("parent_bbox") or geom.get("bbox")
            if not sem_bbox:
                sem_bbox = node.get("bounding_box", []) if isinstance(node, dict) else []
            node_id = getattr(node, "id", None) or (node.get("id") if isinstance(node, dict) else None)
            
            if not sem_text or not sem_bbox:
                continue
                
            # Find matching characters based on IoU and spatial proximity
            matched_chars = []
            fonts_used = set()
            
            # Identify if this is a symbol list_label (like a ZapfDingbats checkbox/bullet)
            is_symbol_label = node_type == "list_label" and not any(c.isalnum() for c in sem_text)
            
            match_bbox = list(sem_bbox)
            if is_symbol_label and len(match_bbox) == 4:
                # Small tolerance to catch list labels without bleeding into adjacent body text
                match_bbox[0] -= 2.0
                match_bbox[2] += 2.0
                match_bbox[1] -= 2.0
                match_bbox[3] += 2.0

            
            for nc in native_chars:
                if nc["id"] in used_chars:
                    continue
                    
                iou = calculate_iou(match_bbox, nc["bbox"])
                # Even if IoU is low, if the character's origin is inside the bbox, it might be a match
                x, y = nc["origin"]
                inside = match_bbox[0] <= x <= match_bbox[2] and match_bbox[1] <= y <= match_bbox[3]
                
                if iou > 0 or inside:
                    matched_chars.append(nc)
                    
            # Sort matched chars by reading order (y then x)
            matched_chars.sort(key=lambda c: (c["origin"][1], c["origin"][0]))
            if matched_chars and sem_text:
                # Filter matched_chars using semantic text anchor
                sem_alnum = [c for c in sem_text if c.isalnum()]
                if sem_alnum:
                    start_idx = -1
                    end_idx = -1
                    sem_idx = 0
                    
                    for i, nc in enumerate(matched_chars):
                        c = nc["text"].strip()
                        if not c:
                            continue
                        if sem_idx < len(sem_alnum) and c.isalnum() and c.lower() == sem_alnum[sem_idx].lower():
                            if start_idx == -1:
                                start_idx = i
                            end_idx = i
                            sem_idx += 1
                            
                    if start_idx != -1 and end_idx != -1 and sem_idx == len(sem_alnum):
                        # Include preceding non-alphanumeric chars (for bullets like 'I' in ZapfDingbats)
                        real_start = start_idx
                        for i in range(start_idx - 1, max(-1, start_idx - 3), -1):
                            prev_c = matched_chars[i]["text"].strip()
                            if prev_c:
                                font_name = matched_chars[i]["font"]
                                is_symbol_font = "Dingbats" in font_name or "Symbol" in font_name
                                if not prev_c.isalnum() or is_symbol_font:
                                    real_start = i
                                else:
                                    break
                        matched_chars = matched_chars[real_start:end_idx+1]
                else:
                    # Semantic text only contains symbols (e.g. bullet points). Find the first exact match sequence.
                    sem_symbols = [c for c in sem_text if c.strip()]
                    
                    if sem_symbols:
                        start_idx = -1
                        end_idx = -1
                        sem_idx = 0
                        
                        for i, nc in enumerate(matched_chars):
                            c = nc["text"].strip()
                            if not c:
                                continue
                                
                            # Safe mapping for ZapfDingbats hollow checkbox (PyMuPDF extracts 0x6E 'n' as 0x49 'I')
                            mapped_c = c
                            if nc["font"] == "ZapfDingbats" and c == "I":
                                mapped_c = "\u2022"
                                
                            if sem_idx < len(sem_symbols) and mapped_c == sem_symbols[sem_idx]:
                                if start_idx == -1:
                                    start_idx = i
                                end_idx = i
                                sem_idx += 1
                                
                        if start_idx != -1 and end_idx != -1 and sem_idx == len(sem_symbols):
                            matched_chars = matched_chars[start_idx:end_idx+1]
                            
            font_runs = []
            confidence = 0.0
            status = "UNMAPPED"
            
            if matched_chars:
                # Mark these characters as consumed so subsequent nodes don't claim them
                used_chars.update(nc["id"] for nc in matched_chars)
                
                # Group matched characters into contiguous font runs
                sem_ptr = 0
                nc_ptr = 0
                current_run = None
                
                while sem_ptr < len(sem_text):
                    c_sem = sem_text[sem_ptr]
                    
                    if not c_sem.strip():
                        # True semantic space from rules_v1.json.
                        # Append it to the current run without modifying any bounding box,
                        # so subsequent text physical positions are not shifted.
                        if current_run:
                            current_run["text"] += c_sem
                        sem_ptr += 1
                        continue
                        
                    # Find the native character that matches c_sem
                    best_nc = None
                    while nc_ptr < len(matched_chars):
                        nc = matched_chars[nc_ptr]
                        nc_text = nc["text"].strip()
                        
                        if not nc_text:
                            # Positional space artifact injected by PyMuPDF due to wide kerning. Skip it.
                            nc_ptr += 1
                            continue
                            
                        # Compare the native character with the semantic character
                        if nc_text.lower() == c_sem.lower():
                            best_nc = nc
                            nc_ptr += 1
                            break
                        elif c_sem.isalnum() and not nc_text.isalnum():
                            # Native character is a control char or stray punctuation (e.g., '\x03'). Skip it.
                            nc_ptr += 1
                            continue
                        else:
                            # Likely a normalized match (e.g., straight vs curly quotes). Accept as best match.
                            best_nc = nc
                            nc_ptr += 1
                            break
                            
                    if not best_nc:
                        # Fallback if matched_chars runs out before sem_text
                        if font_runs and not current_run:
                            best_nc = {"font": font_runs[-1]["font"], "bbox": font_runs[-1]["bbox"], "size": font_runs[-1].get("size", 11.0), "origin": font_runs[-1].get("origin", (font_runs[-1]["bbox"][0], font_runs[-1]["bbox"][1]))}
                        elif current_run:
                            best_nc = {"font": current_run["font"], "bbox": current_run["bbox"], "size": current_run.get("size", 11.0), "origin": current_run.get("origin", (current_run["bbox"][0], current_run["bbox"][1]))}
                        else:
                            fallback_nc = next((n for n in matched_chars if n.get("font")), matched_chars[0] if matched_chars else {})
                            best_nc = {
                                "font": fallback_nc.get("font", "Helvetica"),
                                "bbox": fallback_nc.get("bbox", [0,0,0,0]),
                                "size": fallback_nc.get("size", 11.0),
                                "origin": fallback_nc.get("origin", (0,0))
                            }
                            
                    f_name = best_nc.get("font", "Helvetica")
                    fonts_used.add(f_name)
                    res = resolver.resolve_font_resource(f_name, extracted_data) or "/C0_0"
                    
                    if not current_run:
                        current_run = {
                            "text": c_sem,
                            "font": f_name,
                            "resource": res,
                            "bbox": list(best_nc["bbox"]),
                            "size": best_nc.get("size", 11.0),
                            "origin": best_nc.get("origin", (best_nc["bbox"][0], best_nc["bbox"][1]))
                        }
                    else:
                        # Break run only on font or significant size change
                        if current_run["font"] == f_name and abs(current_run.get("size", 0) - best_nc.get("size", 0)) < 0.5:
                            current_run["text"] += c_sem
                            # Expand run bbox to encompass this matched visible native character.
                            # (Semantic spaces did not affect this bbox.)
                            current_run["bbox"] = [
                                min(current_run["bbox"][0], best_nc["bbox"][0]),
                                min(current_run["bbox"][1], best_nc["bbox"][1]),
                                max(current_run["bbox"][2], best_nc["bbox"][2]),
                                max(current_run["bbox"][3], best_nc["bbox"][3])
                            ]
                        else:
                            font_runs.append(current_run)
                            current_run = {
                                "text": c_sem,
                                "font": f_name,
                                "resource": res,
                                "bbox": list(best_nc["bbox"]),
                                "size": best_nc.get("size", 11.0),
                                "origin": best_nc.get("origin", (best_nc["bbox"][0], best_nc["bbox"][1]))
                            }
                            
                    sem_ptr += 1
                    
                if current_run:
                    font_runs.append(current_run)
                
                # Calculate confidence (Text similarity ratio approximation)
                combined_text_no_spaces = "".join([r["text"] for r in font_runs]).replace(" ", "")
                sem_text_no_spaces = sem_text.replace(" ", "")
                match_count = sum(1 for c in sem_text_no_spaces if c in combined_text_no_spaces)
                  
                # For symbol labels (e.g. bullets), the physical character might not match the semantic symbol.
                if is_symbol_label and font_runs:
                    confidence = 1.0
                else:
                    confidence = match_count / max(len(sem_text_no_spaces), 1)
                
                if confidence >= 0.90:
                    status = "Mapped"
                else:
                    status = "UNMAPPED"
            
            # Record map
            if status == "Mapped":
                font_map.append({
                    "page": extracted_data.get("page_num"),
                    "node_id": node_id,
                    "semantic_text": sem_text,
                    "font_runs": font_runs
                })
                
            # Record report
            report.append({
                "page": extracted_data.get("page_num"),
                "node_id": node_id,
                "text": sem_text,
                "matched_fonts": list(fonts_used),
                "confidence": round(confidence, 2),
                "status": status
            })
            
        return font_map, report
