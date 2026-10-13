from typing import List
from plugins.visual.rule_engine import BaseRule, RuleContext, RuleResult
from models.evidence import EvidenceStatus

class LogoRule(BaseRule):
    """
    Priority 3 Rule.
    Suppresses any geometry (lines or structures) that falls entirely inside a figure or image.
    """
    name = "LogoRule"
    priority = 3

    def evaluate(self, context: RuleContext) -> List[RuleResult]:
        results = []
        
        figure_nodes = [n for n in getattr(context.region, "nodes", []) 
                        if getattr(n, "type", "") in ("figure", "image")]
                        
        if not figure_nodes:
            return results
            
        evidence_map = {ev.id: ev for ev in context.region.evidence}
        
        def is_mostly_inside(bbox1, bbox2):
            """Returns True if >80% of bbox1's area overlaps with bbox2"""
            x_left = max(bbox1[0], bbox2[0])
            y_top = max(bbox1[1], bbox2[1])
            x_right = min(bbox1[2], bbox2[2])
            y_bottom = min(bbox1[3], bbox2[3])

            if x_right < x_left or y_bottom < y_top:
                return False

            intersection_area = (x_right - x_left) * (y_bottom - y_top)
            bbox1_area = max(1.0, (bbox1[2] - bbox1[0]) * (bbox1[3] - bbox1[1]))
            
            # If it's a very thin line, area might be small or 0, so calculate length overlap
            if bbox1[2] - bbox1[0] > bbox1[3] - bbox1[1]:
                # horizontal
                overlap_ratio = (x_right - x_left) / max(1.0, (bbox1[2] - bbox1[0]))
            else:
                # vertical
                overlap_ratio = (y_bottom - y_top) / max(1.0, (bbox1[3] - bbox1[1]))
                
            return (intersection_area / bbox1_area) > 0.8 or overlap_ratio > 0.8
                    
        # Check Structures
        for struct in context.structures:
            active_members = [m for m in struct.member_observation_ids if evidence_map.get(m) and evidence_map[m].status == EvidenceStatus.ACTIVE]
            if not active_members:
                continue
                
            for fig in figure_nodes:
                fig_bbox = getattr(fig, "bbox", [0,0,0,0])
                if is_mostly_inside(struct.bbox, fig_bbox):
                    for obs_id in active_members:
                        results.append(
                            RuleResult(
                                rule_name=self.name,
                                target_evidence_id=obs_id,
                                confidence_delta=-1.0,
                                status_update=EvidenceStatus.SUPPRESSED,
                                reason="Structure located inside a figure/logo."
                            )
                        )
                    break
                    
        # Check remaining isolated lines
        for ev in context.region.evidence:
            if ev.status != EvidenceStatus.ACTIVE:
                continue
            if ev.object_type not in ("horizontal_line", "vertical_line"):
                continue
                
            for fig in figure_nodes:
                fig_bbox = getattr(fig, "bbox", [0,0,0,0])
                if is_mostly_inside(ev.bbox, fig_bbox):
                    results.append(
                        RuleResult(
                            rule_name=self.name,
                            target_evidence_id=ev.id,
                            confidence_delta=-1.0,
                            status_update=EvidenceStatus.SUPPRESSED,
                            reason="Line located inside a figure/logo."
                        )
                    )
                    break
                    
        return results
