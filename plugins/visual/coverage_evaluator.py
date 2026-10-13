import typing
if typing.TYPE_CHECKING:
    from models.observation import Observation
    
from typing import List
from graph.document_graph import DocumentGraph

class CoverageEvaluator:
    """
    Analyzes how much visual coverage was provided by primary analyzers (e.g. VectorAnalyzer).
    Decides if fallback analyzers (RasterAnalyzer) need to be triggered.
    """
    def __init__(self, config=None):
        self.config = config or {}

    def evaluate(self, graph: DocumentGraph, observations: List['Observation'], doc_type: str) -> dict:
        """
        Returns a dict indicating if fallback is required based on missing geometric types.
        """
        # If it's a scanned doc, we expect vectors to fail. We need raster detectors to run.
        # But this is handled by orchestrator routing. Here we just evaluate what we got.
        
        found_types = set(obs.type for obs in observations)
        
        needs_fallback = False
        missing_types = []
        
        # Heuristic: forms usually have horizontal lines (underlines) or rectangles (checkboxes)
        if "rectangle" not in found_types:
            missing_types.append("rectangle")
            needs_fallback = True
            
        if "horizontal_line" not in found_types:
            missing_types.append("horizontal_line")
            needs_fallback = True
            
        return {
            "needs_fallback": needs_fallback,
            "missing_types": missing_types
        }
