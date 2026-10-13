from typing import List
from plugins.visual.rule_engine import BaseRule, RuleContext, RuleResult
from models.evidence import EvidenceStatus

class CheckboxRule(BaseRule):
    """
    Priority 2 Rule.
    Evaluates raster rectangle observations to determine if they are checkboxes.
    """
    name = "CheckboxRule"
    priority = 2

    def evaluate(self, context: RuleContext) -> List[RuleResult]:
        results = []
        for ev in context.region.evidence:
            if ev.status != EvidenceStatus.ACTIVE:
                continue
            if ev.source == "raster_checkbox" and ev.object_type == "rectangle":
                # For Sprint 1, we just return an active confirmation stub
                results.append(
                    RuleResult(
                        rule_name=self.name,
                        target_evidence_id=ev.id,
                        confidence_delta=0.0,
                        status_update=EvidenceStatus.ACTIVE,
                        reason="Stub passed through check."
                    )
                )
        return results
