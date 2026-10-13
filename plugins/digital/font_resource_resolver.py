from typing import Dict, Any, Optional

class FontResourceResolver:
    """
    Acts as the single source of truth for resolving native PDF font resources.
    Maps PyMuPDF font references to PDF internal resource IDs (e.g. /F5).
    """
    
    @staticmethod
    def resolve_font_resource(pymupdf_font_name: str, extracted_page_data: Dict[str, Any]) -> Optional[str]:
        """
        Given a font name from PyMuPDF's span (e.g. 'ArialMT'), find the corresponding /F resource.
        """
        # First, find the font in PyMuPDF's page font list
        basefont_match = None
        resource_name = None
        
        for f in extracted_page_data.get("pymupdf_fonts", []):
            # f is dict with keys: xref, ext, type, basefont, name
            # Note: PyMuPDF's f["name"] often corresponds to the resource name without the slash, e.g. "F5"
            # PyMuPDF's span.font is usually the basefont (without subset tag) or the font name.
            if pymupdf_font_name in (f.get("basefont", ""), f.get("name", "")):
                basefont_match = f.get("basefont", "")
                resource_name = f.get("name", "")
                break
                
            # Handle subset fonts (e.g. ABCDEE+ArialMT)
            bf = f.get("basefont", "")
            if bf and '+' in bf:
                if pymupdf_font_name in bf.split('+')[1]:
                    basefont_match = bf
                    resource_name = f.get("name", "")
                    break

        if not resource_name:
            return None
            
        res_key = f"/{resource_name}"
        
        # Verify it exists in pikepdf resources
        if res_key in extracted_page_data.get("resources", {}):
            return res_key
            
        return res_key
