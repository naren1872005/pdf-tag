import os
import hashlib
import json
from datetime import datetime
from typing import Dict, Any
import pikepdf
import fitz

class DocumentProfiler:
    """
    Stage 0: Document Profiler
    Acts strictly as a passive observer. Detects internal PDF structures and capabilities.
    Generates a DocumentProfile dict.
    """
    
    @staticmethod
    def _compute_hash(filepath: str) -> str:
        sha256_hash = hashlib.sha256()
        try:
            with open(filepath, "rb") as f:
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)
            return sha256_hash.hexdigest()
        except Exception:
            return ""

    @staticmethod
    def profile(pdf_path: str) -> Dict[str, Any]:
        profile = {
            "profile_version": "1.0",
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "generator": "DocumentProfiler",
            "pdf_hash": DocumentProfiler._compute_hash(pdf_path),
            "pdf_size": os.path.getsize(pdf_path) if os.path.exists(pdf_path) else 0,
            "pdf_modified": os.path.getmtime(pdf_path) if os.path.exists(pdf_path) else 0,
            
            "core": {},
            "accessibility": {},
            "content": {},
            "layout": {}
        }

        try:
            pdf = pikepdf.Pdf.open(pdf_path)
            profile["core"]["pdf_version"] = pdf.pdf_version
            profile["core"]["page_count"] = len(pdf.pages)
            profile["core"]["is_encrypted"] = pdf.is_encrypted
            
            # Linearized check
            is_linearized = False
            try:
                # Basic heuristic for linearization check via pikepdf
                if "/Linearized" in pdf.trailer:
                    is_linearized = True
            except Exception:
                pass
            profile["core"]["is_linearized"] = is_linearized

            # Accessibility checks
            is_tagged = False
            if "/MarkInfo" in pdf.Root and "/Marked" in pdf.Root.MarkInfo:
                is_tagged = bool(pdf.Root.MarkInfo.Marked)
            profile["accessibility"]["is_tagged"] = is_tagged
            profile["accessibility"]["has_struct_tree"] = "/StructTreeRoot" in pdf.Root
            profile["accessibility"]["has_parent_tree"] = profile["accessibility"]["has_struct_tree"] and "/ParentTree" in pdf.Root.StructTreeRoot
            profile["accessibility"]["has_acroforms"] = "/AcroForm" in pdf.Root

            has_annotations = False
            global_ctms = []
            
            # Check pages for annotations and global CTMs
            for i, page in enumerate(pdf.pages):
                if "/Annots" in page:
                    has_annotations = True
                
                # Check for naked 'cm' at root of content stream
                try:
                    page.contents_coalesce()
                    stream = page.Contents.read_bytes()
                    stream_str = stream[:100].decode("latin-1", errors="ignore").strip()
                    if " cm" in stream_str and "q" not in stream_str.split(" cm")[0]:
                        global_ctms.append(i + 1)
                except Exception:
                    pass
            
            profile["accessibility"]["has_annotations"] = has_annotations
            profile["layout"]["pages_with_global_ctm"] = global_ctms

        except Exception as e:
            profile["error_pikepdf"] = str(e)

        try:
            doc = fitz.open(pdf_path)
            has_digital_text = False
            has_images = False
            has_vectors = False
            
            mixed_orientation = False
            rotations = []
            mediaboxes = []
            
            for page in doc:
                rot = page.rotation
                rotations.append(rot)
                mediaboxes.append([page.rect.width, page.rect.height])
                
                text_dict = page.get_text("dict")
                for block in text_dict.get("blocks", []):
                    if block.get("type") == 0:
                        has_digital_text = True
                    elif block.get("type") == 1:
                        has_images = True
                
                drawings = page.get_drawings()
                if drawings:
                    has_vectors = True

            profile["content"]["has_digital_text"] = has_digital_text
            profile["content"]["has_images"] = has_images
            profile["content"]["has_vectors"] = has_vectors
            profile["content"]["has_ocr_layer"] = has_digital_text and has_images
            
            # Classification
            if has_digital_text and not has_images:
                profile["document_type"] = "DIGITAL"
            elif has_images and not has_digital_text:
                profile["document_type"] = "SCANNED"
            else:
                profile["document_type"] = "HYBRID"
            
            # Font extraction
            font_list = doc.get_page_fonts(0) if doc.page_count > 0 else []
            # get_page_fonts returns (xref, ext, type, basefont, name)
            fonts_info = []
            font_types = set()
            symbol_fonts = set()
            subset_fonts = set()
            
            for item in font_list:
                if len(item) < 5:
                    continue
                xref, ext, ftype, basefont, name = item[:5]
                font_types.add(ftype)
                if basefont and '+' in basefont:
                    subset_fonts.add(basefont)
                if basefont and any(s in basefont for s in ["ZapfDingbats", "Wingdings", "Symbol"]):
                    symbol_fonts.add(basefont)
                
                f_info = {
                    "resource": name,
                    "basefont": basefont,
                    "type": ftype,
                    "is_subset": '+' in (basefont or ''),
                    "has_tounicode": False,
                    "encoding": ""
                }
                
                # Check pikepdf for ToUnicode and Encoding
                if xref > 0:
                    try:
                        font_obj = pdf.get_object(xref)
                        if "/ToUnicode" in font_obj:
                            f_info["has_tounicode"] = True
                        if "/Encoding" in font_obj:
                            if isinstance(font_obj.Encoding, pikepdf.Name):
                                f_info["encoding"] = str(font_obj.Encoding)
                            elif isinstance(font_obj.Encoding, pikepdf.Dictionary):
                                if "/BaseEncoding" in font_obj.Encoding:
                                    f_info["encoding"] = str(font_obj.Encoding.BaseEncoding)
                    except Exception:
                        pass
                
                fonts_info.append(f_info)

            profile["fonts"] = {
                "list": fonts_info,
                "types_used": list(font_types),
                "subset_fonts": list(subset_fonts),
                "symbol_fonts": list(symbol_fonts),
                "count": len(fonts_info)
            }
            
            profile["layout"]["rotations"] = list(set(rotations))
            profile["layout"]["mixed_orientation"] = len(set(rotations)) > 1 or len(set([f"{w}x{h}" for w, h in mediaboxes])) > 1

        except Exception as e:
            profile["error_fitz"] = str(e)

        return profile
