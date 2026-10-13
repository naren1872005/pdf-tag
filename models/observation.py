import uuid
from typing import List

class Observation:
    """
    A raw visual finding from an analyzer (e.g. 'Rectangle', 'Horizontal Line').
    Observations are strictly geometric/physical, NOT semantic interpretations.
    """
    def __init__(self, obs_type: str, page: int, bbox: List[float], source: str, confidence: float = 1.0):
        self.id = str(uuid.uuid4())
        self.type = obs_type # e.g. 'rectangle', 'line'
        self.page = page
        self.bbox = bbox # [x0, y0, x1, y1]
        self.confidence = confidence
        self.source = source # e.g. 'vector', 'raster_underline', 'raster_checkbox'
        
    def to_dict(self):
        return {
            "id": self.id,
            "type": self.type,
            "page": self.page,
            "bbox": self.bbox,
            "confidence": self.confidence,
            "source": self.source
        }
