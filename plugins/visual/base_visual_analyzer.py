from abc import ABC, abstractmethod
from typing import List, Dict, Any
from graph.document_graph import DocumentGraph
from models.observation import Observation
from plugins.base_plugin import BasePlugin

class BaseVisualAnalyzer(BasePlugin, ABC):
    """
    Standard interface for all visual analyzers (Vector, Raster, ML, etc.).
    """
    def run(self, context, data=None):
        """
        Adapts the BasePlugin signature. Should not be used directly by VisualOrchestrator.
        """
        pass

    @abstractmethod
    def analyze(self, document_graph: DocumentGraph, pdf_path: str) -> List[Observation]:
        """
        Scans the document and returns a flat list of physical Observations.
        Must not mutate the document_graph.
        """
        pass
