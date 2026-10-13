import importlib
import inspect
import pkgutil
from typing import List, Optional
from models.visual_region import VisualRegion
from models.evidence import Evidence, EvidenceStatus
import plugins.visual.rules

class RuleResult:
    """Immutable DTO for a rule's decision on a specific evidence."""
    def __init__(self, rule_name: str, target_evidence_id: str, 
                 confidence_delta: float = 0.0, 
                 status_update: Optional[EvidenceStatus] = None, 
                 reason: str = ""):
        self.rule_name = rule_name
        self.target_evidence_id = target_evidence_id
        self.confidence_delta = confidence_delta
        self.status_update = status_update
        self.reason = reason

class RuleContext:
    """Immutable context passed to every rule."""
    def __init__(self, region: VisualRegion, config: dict, structures=None):
        self.region = region
        self.config = config
        self.structures = structures or []

class BaseRule:
    """Base class for all visual heuristic rules."""
    name: str = "BaseRule"
    priority: int = 999
    
    def evaluate(self, context: RuleContext) -> List[RuleResult]:
        raise NotImplementedError

class RuleEngine:
    """
    Discovers and executes all rule plugins on a region.
    Evaluates rules sequentially based on priority.
    Applies RuleResults to Evidence non-destructively.
    """
    def __init__(self, config=None):
        self.config = config or {}
        # Load rules configuration if not provided
        if not self.config.get("rules"):
            try:
                import yaml
                import os
                config_path = os.path.join(os.path.dirname(__file__), "..", "..", "config", "rules.yaml")
                with open(config_path, "r", encoding="utf-8") as f:
                    self.config["rules"] = yaml.safe_load(f)
            except Exception as e:
                print(f"Warning: Could not load rules.yaml - {e}")
                self.config["rules"] = {}
                
        self.rules: List[BaseRule] = self._discover_rules()
        
    def _discover_rules(self) -> List[BaseRule]:
        rules = []
        package = plugins.visual.rules
        for _, module_name, _ in pkgutil.iter_modules(package.__path__):
            full_module_name = f"{package.__name__}.{module_name}"
            module = importlib.import_module(full_module_name)
            for name, obj in inspect.getmembers(module, inspect.isclass):
                if issubclass(obj, BaseRule) and obj is not BaseRule:
                    rules.append(obj())
        # Sort by priority ascending
        rules.sort(key=lambda r: r.priority)
        return rules

    def evaluate(self, regions: List[VisualRegion]):
        """Runs all rules on all regions and applies state changes non-destructively."""
        from plugins.visual.structure_detector import StructureDetector
        structure_detector = StructureDetector(self.config)
        
        for region in regions:
            page = region.evidence[0].page if region.evidence else 1
            structures = structure_detector.detect(region.evidence, page)
            
            context = RuleContext(region, self.config, structures)
            
            for rule in self.rules:
                try:
                    results = rule.evaluate(context)
                    if not results:
                        continue
                        
                    # Apply results (The Engine is the only mutator)
                    for result in results:
                        # Find the target evidence
                        target_ev = next((ev for ev in region.evidence if ev.id == result.target_evidence_id), None)
                        if target_ev:
                            # Apply delta
                            target_ev.confidence += result.confidence_delta
                            target_ev.confidence = max(0.0, min(1.0, target_ev.confidence)) # Clamp between 0 and 1
                            
                            # Apply status update
                            if result.status_update:
                                target_ev.status = result.status_update
                                
                            # Append trace
                            trace_entry = f"{result.rule_name}: delta={result.confidence_delta}, status={result.status_update.name if result.status_update else 'UNCHANGED'}. Reason: {result.reason}"
                            target_ev.history.append(trace_entry)
                        else:
                            print(f"Warning: Rule {result.rule_name} target evidence {result.target_evidence_id} not found!")
                            
                except Exception as e:
                    print(f"RuleEngine Error in {rule.name}: {e}")
