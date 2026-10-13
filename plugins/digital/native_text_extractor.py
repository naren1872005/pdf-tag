import fitz
import pikepdf
from typing import Dict, Any, List

class NativeTextExtractor:
    """
    Extracts native text directly from the Digital PDF at the glyph level.
    Uses PyMuPDF (fitz) get_text("rawdict") to collect characters and spans.
    Uses Pikepdf to inspect page resources and fonts.
    This module performs extraction only.
    """
    
    @staticmethod
    def extract_page(pdf_path: str, page_num: int) -> Dict[str, Any]:
        """
        page_num is 1-indexed.
        """
        result = {
            "page_num": page_num,
            "blocks": [],
            "resources": {}
        }
        
        # 1. PyMuPDF text extraction
        try:
            doc = fitz.open(pdf_path)
            if 1 <= page_num <= doc.page_count:
                page = doc[page_num - 1]
                result["page_width"] = page.rect.width
                result["page_height"] = page.rect.height
                # PyMuPDF text coordinates are already relative to the CropBox
                # (page.rect), i.e. the same frame as the JSON boxes: no offset.
                result["crop_x0"] = 0.0
                result["crop_y0"] = 0.0
                
                rawdict = page.get_text("rawdict")
                blocks = rawdict.get("blocks", [])
                # PyMuPDF text coordinates are in the UNROTATED page; the JSON boxes
                # describe the page as displayed. Rotate them into the displayed frame.
                if page.rotation:
                    R = page.rotation_matrix
                    for b in blocks:
                        for l in b.get("lines", []):
                            for s in l.get("spans", []):
                                for c in s.get("chars", []):
                                    c["bbox"] = tuple(fitz.Rect(c["bbox"]) * R)
                                    o = fitz.Point(c["origin"]) * R
                                    c["origin"] = (o.x, o.y)
                result["blocks"] = blocks
                
                # Get fonts list for the page to map font names
                font_list = doc.get_page_fonts(page_num - 1)
                result["pymupdf_fonts"] = [
                    {
                        "xref": f[0],
                        "ext": f[1],
                        "type": f[2],
                        "basefont": f[3],
                        "name": f[4]
                    } for f in font_list
                ]
            doc.close()
        except Exception as e:
            result["error_fitz"] = str(e)
            
        # 2. Pikepdf resource inspection
        try:
            pdf = pikepdf.Pdf.open(pdf_path)
            if 1 <= page_num <= len(pdf.pages):
                page_obj = pdf.pages[page_num - 1]
                if "/Resources" in page_obj and "/Font" in page_obj.Resources:
                    fonts_dict = page_obj.Resources.Font
                    for res_name, font_obj in fonts_dict.items():
                        font_info = {
                            "resource": str(res_name),
                            "basefont": str(font_obj.get("/BaseFont", "")),
                            "subtype": str(font_obj.get("/Subtype", "")),
                            "has_tounicode": "/ToUnicode" in font_obj,
                            "encoding": ""
                        }
                        if "/Encoding" in font_obj:
                            enc = font_obj.Encoding
                            if isinstance(enc, pikepdf.Name):
                                font_info["encoding"] = str(enc)
                            elif isinstance(enc, pikepdf.Dictionary) and "/BaseEncoding" in enc:
                                font_info["encoding"] = str(enc.BaseEncoding)
                        result["resources"][str(res_name)] = font_info
            pdf.close()
        except Exception as e:
            result["error_pikepdf"] = str(e)
            
        return result
