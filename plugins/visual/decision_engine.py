from typing import List
from models.visual_region import VisualRegion

class DecisionEngine:
    """
    Decides whether a region needs to be escalated to Gemini.
    Evaluates confidence, complexity, profile, and conflicts.
    """
    def __init__(self, config=None):
        self.config = config or {}
        # Configurable thresholds
        self.confidence_threshold = self.config.get("gemini_confidence_threshold", 0.85)
        
    def needs_escalation(self, region: VisualRegion) -> bool:
        """
        Returns True if the region requires Gemini reasoning.
        """
        # 1. Skip pure empty space
        if len(region.nodes) == 0 and len(region.evidence) == 0:
            return False
            
        # 2. Skip pure text paragraphs (no visual evidence)
        if len(region.nodes) > 0 and len(region.evidence) == 0:
            return False
            
        # 3. Check overall confidence
        overall_conf = region.confidence.get("overall", 0.0)
        if overall_conf < self.confidence_threshold:
            return True
            
        # 4. Check complexity
        complexity = region.profile.get("complexity", "unknown")
        if complexity == "high":
            return True
            
        # 5. Check for conflicts
        # Example: multiple rules disagreeing, or OpenCV says rectangle but Vector says line
        # For Phase 1, if we got this far with high confidence, we trust it.
        
        return False
