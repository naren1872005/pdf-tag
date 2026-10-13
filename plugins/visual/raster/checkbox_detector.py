import cv2
import numpy as np
import fitz # PyMuPDF
from typing import List
from graph.document_graph import DocumentGraph
from models.observation import Observation
from plugins.visual.base_visual_analyzer import BaseVisualAnalyzer

class CheckboxDetector(BaseVisualAnalyzer):
    """
    Raster Analyzer: Detects checkbox squares using OpenCV contour analysis.
    """
    def analyze(self, document_graph: DocumentGraph, pdf_path: str) -> List[Observation]:
        observations: List[Observation] = []
        if not pdf_path:
            return observations
            
        try:
            doc = fitz.open(pdf_path)
            for page_num in range(len(doc)):
                page = doc[page_num]
                
                # Rasterize at 200 DPI
                pix = page.get_pixmap(dpi=200)
                img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
                
                if pix.n == 3:
                    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
                elif pix.n == 4:
                    gray = cv2.cvtColor(img, cv2.COLOR_RGBA2GRAY)
                else:
                    gray = img
                    
                # Threshold to binary (invert)
                _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)
                
                # Find contours
                contours, _ = cv2.findContours(thresh, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
                
                scale = 72.0 / 200.0
                
                for cnt in contours:
                    # Approximate contour to polygon
                    peri = cv2.arcLength(cnt, True)
                    approx = cv2.approxPolyDP(cnt, 0.04 * peri, True)
                    
                    if len(approx) == 4:
                        x, y, w, h = cv2.boundingRect(approx)
                        aspect_ratio = float(w) / h
                        # Differentiate between standard square checkboxes and large wide rectangles (like next to MAY 19)
                        is_square = (0.8 <= aspect_ratio <= 1.2) and (20 <= w <= 80)
                        is_wide_box = (1.5 <= aspect_ratio <= 3.0) and (45 <= w <= 80)
                        
                        if is_square or is_wide_box:
                            page_height = page.rect.height
                            pdf_bbox = [x * scale, page_height - ((y + h) * scale), (x + w) * scale, page_height - (y * scale)]
                            obs = Observation("rectangle", page_num + 1, pdf_bbox, "raster_checkbox")
                            observations.append(obs)
                            
            doc.close()
        except Exception as e:
            print(f"CheckboxDetector Error: {e}")
            
        return observations
