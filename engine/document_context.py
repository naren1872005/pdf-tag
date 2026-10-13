from typing import Dict, Any, List
from graph.document_graph import DocumentGraph

class DocumentContext:
    """
    Holds the global state of the document being processed.
    This includes the document graph and document-wide statistics.
    """
    def __init__(self):
        self.graph = DocumentGraph()
        self.profile: Dict[str, Any] = {}
        self.stats: Dict[str, Any] = {
            "fonts": {},
            "headings": [],
            "tables": [],
            "figures": [],
            "paragraphs": [],
            "headers": [],
            "footers": [],
            "forms": [],
            "lists": [],
            "reading_direction": "L2R",
            "page_templates": {},
            "layout_regions": []
        }
        self.metadata: Dict[str, Any] = {}
