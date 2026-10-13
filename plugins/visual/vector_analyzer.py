import fitz # PyMuPDF
from typing import List
from graph.document_graph import DocumentGraph
from models.observation import Observation
from plugins.visual.base_visual_analyzer import BaseVisualAnalyzer

class VectorAnalyzer(BaseVisualAnalyzer):
    """
    Primary Analyzer: Extracts exact vector graphics (drawings) using PyMuPDF.
    Yields raw Observations, not interpreted objects.
    """
    def analyze(self, document_graph: DocumentGraph, pdf_path: str) -> List[Observation]:
        observations: List[Observation] = []
        if not pdf_path:
            return observations
            
        try:
            doc = fitz.open(pdf_path)
            for page_num in range(len(doc)):
                page = doc[page_num]
                drawings = page.get_drawings()
                
                # Basic iteration over vector paths. 
                for draw in drawings:
                    rect = draw.get("rect")
                    if not rect: continue
                    
                    # Ignore complex text outlines (which contain bezier curves 'c')
                    # Form lines and checkboxes are made of lines ('l') and rects ('re')
                    has_curve = any(item[0] == 'c' for item in draw.get("items", []))
                    if has_curve: continue
                    
                    page_height = page.rect.height
                    # Convert PyMuPDF Top-Left to standard PDF Bottom-Left
                    bbox = [rect.x0, page_height - rect.y1, rect.x1, page_height - rect.y0]
                    width = rect.x1 - rect.x0
                    height = rect.y1 - rect.y0
                    
                    # Yield raw geometric properties, let the Fusion Engine decide meaning
                    if height < 3 and width > 20:
                        obs = Observation("horizontal_line", page_num + 1, bbox, "vector")
                        observations.append(obs)
                    elif 5 < width < 30 and 5 < height < 30 and abs(width - height) < 5:
                        obs = Observation("rectangle", page_num + 1, bbox, "vector")
                        observations.append(obs)
                        
            doc.close()
        except Exception as e:
            print(f"VectorAnalyzer Error: {e}")
            
        return observations
