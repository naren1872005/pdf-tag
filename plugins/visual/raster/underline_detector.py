import cv2
import numpy as np
import fitz # PyMuPDF
from typing import List
from graph.document_graph import DocumentGraph
from models.observation import Observation
from plugins.visual.base_visual_analyzer import BaseVisualAnalyzer

class UnderlineDetector(BaseVisualAnalyzer):
    """
    Raster Analyzer: Detects continuous horizontal and vertical lines using OpenCV morphology.
    Produces raw observations for the StructureDetector.
    """
    def analyze(self, document_graph: DocumentGraph, pdf_path: str) -> List[Observation]:
        observations: List[Observation] = []
        if not pdf_path:
            return observations
            
        config = self.config.get("rules", {}).get("underline", {})
        min_length = config.get("min_length", 30)
        close_k = config.get("close_kernel", 5)
        open_k = config.get("open_kernel", 30)
        
        try:
            doc = fitz.open(pdf_path)
            for page_num in range(len(doc)):
                page = doc[page_num]
                
                # Rasterize page at 200 DPI
                pix = page.get_pixmap(dpi=200)
                img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
                
                # Convert to grayscale
                if pix.n == 3:
                    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
                elif pix.n == 4:
                    gray = cv2.cvtColor(img, cv2.COLOR_RGBA2GRAY)
                else:
                    gray = img
                    
                # Threshold to binary (invert so lines are white)
                _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)
                
                # Close gaps in faint/dotted lines
                if close_k > 0:
                    c_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (close_k, close_k))
                    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, c_kernel)
                
                scale = 72.0 / 200.0
                
                # --- HORIZONTAL LINES ---
                if open_k > 0:
                    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (open_k, 1))
                    horizontal = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, h_kernel)
                    h_contours, _ = cv2.findContours(horizontal, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    
                    for cnt in h_contours:
                        x, y, w, h = cv2.boundingRect(cnt)
                        if w >= min_length and h < 10: 
                            page_height = page.rect.height
                            # Convert to bottom-left
                            pdf_bbox = [x * scale, page_height - ((y + h) * scale), (x + w) * scale, page_height - (y * scale)]
                            obs = Observation("horizontal_line", page_num + 1, pdf_bbox, "raster_underline")
                            observations.append(obs)
                            
                # --- VERTICAL LINES ---
                if open_k > 0:
                    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, open_k))
                    vertical = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, v_kernel)
                    v_contours, _ = cv2.findContours(vertical, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    
                    for cnt in v_contours:
                        x, y, w, h = cv2.boundingRect(cnt)
                        if h >= min_length and w < 10:
                            page_height = page.rect.height
                            # Convert to bottom-left
                            pdf_bbox = [x * scale, page_height - ((y + h) * scale), (x + w) * scale, page_height - (y * scale)]
                            obs = Observation("vertical_line", page_num + 1, pdf_bbox, "raster_vertical")
                            observations.append(obs)
                            
            doc.close()
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"UnderlineDetector Error: {e}")
            
        return observations
