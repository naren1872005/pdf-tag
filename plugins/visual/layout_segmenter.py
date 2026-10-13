from typing import List
from models.visual_region import VisualRegion
import fitz # PyMuPDF

class LayoutSegmenter:
    """
    Slices a document page into structural regions based on visual layout and whitespace.
    Strictly independent of OCR text.
    """
    def __init__(self, config=None):
        self.config = config or {}
        
    def segment(self, pdf_path: str) -> List[VisualRegion]:
        """
        Segments all pages in the PDF into VisualRegions.
        """
        regions: List[VisualRegion] = []
        if not pdf_path:
            return regions
            
        try:
            doc = fitz.open(pdf_path)
            for page_num in range(len(doc)):
                page = doc[page_num]
                rect = page.rect
                
                # For Phase 1, we treat the entire page bounding box as a single region
                # Future versions will use OpenCV projection profiles to split the page 
                # horizontally into blocks (e.g., header, main body, footer).
                
                # Create a single structural region for the whole page (for now)
                # bbox format: [x0, y0, x1, y1]
                bbox = [rect.x0, rect.y0, rect.x1, rect.y1]
                region = VisualRegion(page=page_num + 1, bbox=bbox)
                region.profile["region_type"] = "full_page"
                
                regions.append(region)
            doc.close()
        except Exception as e:
            print(f"LayoutSegmenter Error: {e}")
            
        return regions
