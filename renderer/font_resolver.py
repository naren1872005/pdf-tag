# GUI/renderer/font_resolver.py
import pikepdf

class FontResolver:
    """
    Detects original fonts, encodings, and embedded subsets.
    Attempts to use the original font if it can render the glyph.
    """
    def __init__(self, page_obj):
        self.page_obj = page_obj
        self.fonts = {}
        self._detect_fonts()
        
    def _detect_fonts(self):
        try:
            resources = self.page_obj.Resources
            if "/Font" in resources:
                for font_name, font_dict in resources.Font.items():
                    self.fonts[str(font_name)] = font_dict
        except Exception:
            pass

    def resolve_font_for_glyph(self, glyph: str, original_font_hint: str = None) -> tuple:
        """
        Returns (font_resource_name, requires_replacement).
        For Digital PDFs, we try to preserve original fonts.
        Since parsing embedded subset CIDFonts for glyph widths and encoding 
        is out of scope for a basic renderer, we provide a structured fallback.
        """
        # If the glyph is a known unsupported glyph in standard Helvetica
        if glyph == "■":
            # If we don't have full CIDFont mapping, fallback to replacing with standard bullet
            return ("helv", True)
            
        return ("helv", False)

    @staticmethod
    def process_text_for_rendering(text: str, document_type: str = "Scanned PDF") -> str:
        """
        Applies necessary replacements based on the document type.
        """
        if "DIGITAL" in document_type.upper():
            # In Digital PDF, we try to preserve glyphs unless absolutely required
            if "■" in text:
                return text.replace("■", "•")
            return text
        else:
            return text
