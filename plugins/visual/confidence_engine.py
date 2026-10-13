from typing import List
from models.visual_region import VisualRegion

class ConfidenceEngine:
    """
    Centralized confidence calculator.
    Aggregates confidence from all Evidence sources in the region.
    Populates VisualRegion.confidence.
    """
    def __init__(self, config=None):
        self.config = config or {}
        
    def calculate(self, regions: List[VisualRegion]):
        """
        Updates the confidence profile of each region based on its evidence.
        """
        for region in regions:
            conf_metrics = {
                "vector": [],
                "raster": [],
                "rule": [],
                "gemini": []
            }
            
            for ev in region.evidence:
                if "vector" in ev.source:
                    conf_metrics["vector"].append(ev.confidence)
                elif "raster" in ev.source:
                    conf_metrics["raster"].append(ev.confidence)
                elif "rule" in ev.source:
                    conf_metrics["rule"].append(ev.confidence)
                elif "gemini" in ev.source:
                    conf_metrics["gemini"].append(ev.confidence)
                    
            # Calculate averages (if any)
            for key, vals in conf_metrics.items():
                if vals:
                    region.confidence[key] = sum(vals) / len(vals)
                else:
                    region.confidence[key] = 0.0
                    
            # Overall confidence heuristic
            # (In a real system, this could be a weighted average)
            all_confs = []
            for key in ["vector", "raster", "rule", "gemini"]:
                if region.confidence.get(key, 0.0) > 0:
                    all_confs.append(region.confidence[key])
                    
            if all_confs:
                region.confidence["overall"] = sum(all_confs) / len(all_confs)
            else:
                region.confidence["overall"] = 0.0
