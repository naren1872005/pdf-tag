from typing import List, Dict, Type
from plugins.visual.base_visual_analyzer import BaseVisualAnalyzer

class VisualAnalyzerRegistry:
    """
    Dynamically manages and instantiates all visual analyzers.
    This allows us to add new analyzers (e.g. SignatureAnalyzer, MLAnalyzer)
    without modifying the orchestrator.
    """
    def __init__(self, config=None):
        self.config = config or {}
        self.analyzers: List[BaseVisualAnalyzer] = []
        
        self._register_default_analyzers()
        
    def _register_default_analyzers(self):
        # In a real system, this could use importlib to dynamically scan the visual/ directory.
        from plugins.visual.vector_analyzer import VectorAnalyzer
        # We will import raster detectors once they are built.
        from plugins.visual.raster.underline_detector import UnderlineDetector
        from plugins.visual.raster.checkbox_detector import CheckboxDetector
        
        self.register(VectorAnalyzer)
        self.register(UnderlineDetector)
        self.register(CheckboxDetector)
        
    def register(self, analyzer_class: Type[BaseVisualAnalyzer]):
        """Instantiates and registers an analyzer class."""
        self.analyzers.append(analyzer_class(self.config))
        
    def get_all(self) -> List[BaseVisualAnalyzer]:
        return self.analyzers
