import uuid
from typing import Dict, Any, List

class Node:
    """
    Represents a single structural element within the document.
    Uses strict namespaces (semantic, visual, geometry, relations, metadata, analysis)
    to enforce Single Responsibility and keep the graph clean.
    """
    def __init__(self, raw_data: Dict[str, Any]):
        self.id = raw_data.get('id')
        self.uuid = raw_data.get('uuid', str(uuid.uuid4()))
        self.text = raw_data.get('text', raw_data.get('content', ''))
        
        # 1. Semantic (Assigned by Normalization and Detectors)
        # Preserve all extra fields from raw_data (like heading_level, image_path, alt_text)
        self.semantic = raw_data.get('semantic', {})
        if not self.semantic:
            for k, v in raw_data.items():
                if k not in ('id', 'uuid', 'content', 'bounding_box', 'visual', 'geometry', 'relations', 'metadata', 'analysis', 'status', 'history', 'kids'):
                    self.semantic[k] = v
            self.semantic.setdefault("type", raw_data.get('semantic_type', raw_data.get('type', 'paragraph')))
            self.semantic.setdefault("confidence", raw_data.get('confidence', 0.0))
            self.semantic.setdefault("pdf_tag", raw_data.get('pdf_tag', None))
        
        # 2. Visual (Assigned by Visual Analysis)
        self.visual = raw_data.get('visual', {
            "widgets": []
        })
        
        # 3. Geometry (Assigned by Normalization and Geometry Analysis)
        self.geometry = raw_data.get('geometry', {
            "bbox": raw_data.get('bounding_box', []),
            "width": 0, "height": 0, "center": [0,0],
            "area": 0, "relative_x": 0, "relative_y": 0,
            "row": None,
            "column": None,
            "alignment": None
        })
        
        # 4. Relations (Assigned by Semantic Analysis and Consistency Analysis)
        self.relations = raw_data.get('relations', {
            "parent": None,      # UUID string
            "children": [],      # List of UUID strings
            "previous": None,    # UUID string
            "next": None,        # UUID string
            "table": None,       # UUID string
            "caption_of": None,  # UUID string
            "neighbors": {}      # Dict of direction to UUID
        })
        
        # 5. Metadata (Page mapping, etc.)
        self.metadata = raw_data.get('metadata', {
            "page": raw_data.get('page_number')
        })
        
        # 6. Analysis (For notes, warnings, flags)
        self.analysis = raw_data.get('analysis', {
            "notes": []
        })
        
        # Tracking
        self.status = raw_data.get('status', {
            "normalized": False,
            "geometry_analyzed": False,
            "visual_analyzed": False,
            "semantic_detected": False,
            "semantic_analyzed": False,
            "consistency_analyzed": False,
            "qa_passed": False,
            "validated": False
        })
        self.history = raw_data.get('history', ["Created Document Graph Node"])
        
    @property
    def page(self):
        return self.metadata.get('page')
        
    def add_history(self, event: str):
        self.history.append(event)
        
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "uuid": self.uuid,
            "text": self.text,
            "semantic": self.semantic,
            "visual": self.visual,
            "geometry": self.geometry,
            "relations": self.relations,
            "metadata": self.metadata,
            "analysis": self.analysis,
            "status": self.status,
            "history": self.history
        }
