from typing import List
from plugins.visual.rule_engine import BaseRule, RuleContext, RuleResult
from models.evidence import EvidenceStatus, Evidence
import uuid

class TableRule(BaseRule):
    """
    Priority 1 Rule.
    Consumes Structures and evaluates their geometric features.
    If a structure has multiple rows/cols/intersections, it classifies it as a table and suppresses its member lines.
    """
    name = "TableRule"
    priority = 1

    def evaluate(self, context: RuleContext) -> List[RuleResult]:
        results = []
        
        table_config = context.config.get("rules", {}).get("table", {})
        min_intersections = table_config.get("min_intersections", 4)
        
        for struct in context.structures:
            features = struct.features
            h_lines = features.get("horizontal_lines", 0)
            v_lines = features.get("vertical_lines", 0)
            intersections = features.get("intersections", 0)
            
            # A table typically has multiple horizontal and vertical lines with many intersections
            if h_lines >= 3 and v_lines >= 2 and intersections >= min_intersections:
                # Add a new Evidence for the Table
                table_ev = Evidence(
                    source="table_rule",
                    object_type="table",
                    bbox=struct.bbox,
                    page=struct.page,
                    confidence=1.0
                )
                table_ev.id = str(uuid.uuid4())
                table_ev.history.append(f"Classified by TableRule based on geometry: {h_lines}H, {v_lines}V, {intersections}X")
                context.region.evidence.append(table_ev)
                
                # Suppress the member lines so they aren't used for form fields
                for obs_id in struct.member_observation_ids:
                    results.append(
                        RuleResult(
                            rule_name=self.name,
                            target_evidence_id=obs_id,
                            confidence_delta=-1.0,
                            status_update=EvidenceStatus.SUPPRESSED,
                            reason="Member of a classified table structure."
                        )
                    )
                    
        return results
