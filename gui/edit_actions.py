from typing import Dict, Any, List

class GraphAction:
    def apply(self, elements: List[Dict[str, Any]]) -> bool:
        raise NotImplementedError

    def undo(self, elements: List[Dict[str, Any]]) -> bool:
        raise NotImplementedError

def _get_node(elements: List[Dict[str, Any]], node_id: str) -> Dict[str, Any]:
    for el in elements:
        if str(el.get('id')) == str(node_id) or str(el.get('node_id')) == str(node_id) or str(el.get('uuid')) == str(node_id):
            return el
        if 'kids' in el:
            found = _get_node(el['kids'], node_id)
            if found: return found
    return None

class UpdateTextAction(GraphAction):
    def __init__(self, node_id, new_txt, old_txt):
        self.node_id = node_id
        self.new_text = new_txt
        self.old_text = old_txt
        
    def apply(self, elements: List[Dict[str, Any]]) -> bool:
        node = _get_node(elements, self.node_id)
        if node:
            if 'content' in node: node['content'] = self.new_text
            else: node['text'] = self.new_text
            if 'semantic' in node and isinstance(node['semantic'], dict):
                node['semantic']['content'] = self.new_text
            if 'history' not in node: node['history'] = []
            node['history'].append(f"Manual GUI Edit: Changed text to '{self.new_text}'")
        return True
        
    def undo(self, elements: List[Dict[str, Any]]) -> bool:
        node = _get_node(elements, self.node_id)
        if node:
            if 'content' in node: node['content'] = self.old_text
            else: node['text'] = self.old_text
            if 'semantic' in node and isinstance(node['semantic'], dict):
                node['semantic']['content'] = self.old_text
        return True

class UpdateRoleAction(GraphAction):
    def __init__(self, node_id: str, old_role: str, new_role: str):
        self.node_id = node_id
        self.old_role = old_role
        self.new_role = new_role

    def _set_canonical_role(self, el: Dict[str, Any], role: str):
        if 'role' in el:
            el['role'] = role
        elif 'semantic' in el:
            el['semantic']['type'] = role
        elif 'semantic_type' in el:
            el['semantic_type'] = role
        else:
            el['type'] = role

    def apply(self, elements: List[Dict[str, Any]]) -> bool:
        node = _get_node(elements, self.node_id)
        if not node:
            return False
        
        self._set_canonical_role(node, self.new_role)
        
        # Add history track on node itself if it supports it
        if 'history' not in node:
            node['history'] = []
        node['history'].append(f"Manual GUI Edit: Changed role to {self.new_role}")
        return True

    def undo(self, elements: List[Dict[str, Any]]) -> bool:
        node = _get_node(elements, self.node_id)
        if not node:
            return False
        
        self._set_canonical_role(node, self.old_role)
        
        if 'history' not in node:
            node['history'] = []
        node['history'].append(f"Manual GUI Edit (Undo): Restored role to {self.old_role}")
        return True

class CreateTagAction(GraphAction):
    def __init__(self, node_id: str, node_dict: Dict[str, Any]):
        self.node_id = node_id
        self.node_dict = node_dict

    def apply(self, elements: List[Dict[str, Any]]) -> bool:
        node = _get_node(elements, self.node_id)
        if node:
            return False
        elements.append(self.node_dict)
        return True

    def undo(self, elements: List[Dict[str, Any]]) -> bool:
        for i, el in enumerate(elements):
            if str(el.get('id')) == str(self.node_id) or str(el.get('node_id')) == str(self.node_id) or str(el.get('uuid')) == str(self.node_id):
                del elements[i]
                return True
        return False

class UpdateBBoxAction(GraphAction):
    def __init__(self, node_id: str, old_bbox: List[float], new_bbox: List[float]):
        self.node_id = node_id
        self.old_bbox = old_bbox
        self.new_bbox = new_bbox

    def _set_canonical_bbox(self, el: Dict[str, Any], bbox: List[float]):
        if 'geometry' in el and 'bbox' in el['geometry']:
            el['geometry']['bbox'] = bbox
        elif 'bounding_box' in el:
            el['bounding_box'] = bbox
        else:
            if 'geometry' not in el:
                el['geometry'] = {}
            el['geometry']['bbox'] = bbox

    def apply(self, elements: List[Dict[str, Any]]) -> bool:
        node = _get_node(elements, self.node_id)
        if not node:
            return False
        
        self._set_canonical_bbox(node, self.new_bbox)
        
        if 'history' not in node:
            node['history'] = []
        node['history'].append(f"Manual GUI Edit: Changed bbox to {self.new_bbox}")
        return True

    def undo(self, elements: List[Dict[str, Any]]) -> bool:
        node = _get_node(elements, self.node_id)
        if not node:
            return False
        
        self._set_canonical_bbox(node, self.old_bbox)
        
        if 'history' not in node:
            node['history'] = []
        node['history'].append(f"Manual GUI Edit (Undo): Restored bbox to {self.old_bbox}")
        return True

class RemoveTagAction(GraphAction):
    def __init__(self, node_id: str, node_dict: Dict[str, Any]):
        self.node_id = node_id
        self.node_dict = node_dict

    def apply(self, elements: List[Dict[str, Any]]) -> bool:
        def _remove(elem_list):
            for i, el in enumerate(elem_list):
                curr_id = el.get('id') or el.get('node_id') or el.get('uuid')
                if str(curr_id) == str(self.node_id):
                    del elem_list[i]
                    return True
                if 'kids' in el and isinstance(el['kids'], list):
                    if _remove(el['kids']):
                        return True
            return False
        return _remove(elements)

    def undo(self, elements: List[Dict[str, Any]]) -> bool:
        node = _get_node(elements, self.node_id)
        if node:
            return False
        elements.append(self.node_dict)
        return True

class BatchRemoveTagAction(GraphAction):
    def __init__(self, node_dicts: List[Dict[str, Any]]):
        self.node_dicts = node_dicts

    def apply(self, elements: List[Dict[str, Any]]) -> bool:
        def _remove(elem_list, n_id):
            for i, el in enumerate(elem_list):
                curr_id = el.get('id') or el.get('node_id') or el.get('uuid')
                if str(curr_id) == str(n_id):
                    del elem_list[i]
                    return True
                if 'kids' in el and isinstance(el['kids'], list):
                    if _remove(el['kids'], n_id):
                        return True
            return False
            
        success = False
        for node_dict in self.node_dicts:
            n_id = node_dict.get('id') or node_dict.get('node_id') or node_dict.get('uuid')
            if n_id and _remove(elements, n_id):
                success = True
        return success

    def undo(self, elements: List[Dict[str, Any]]) -> bool:
        for node_dict in self.node_dicts:
            n_id = node_dict.get('id') or node_dict.get('node_id') or node_dict.get('uuid')
            if not _get_node(elements, n_id):
                elements.append(node_dict)
        return True


class DeleteStructTagsAction(GraphAction):
    """Tags panel -> Delete Tag. Acts on the tagged PDF's structure tree (the tag
    and all its descendant tags), not on the JSON elements, so apply()/undo() on
    the element list are no-ops. The main window performs the PDF change:
      * snapshot  - the PDF bytes before the deletion (exact undo in this session)
      * targets   - [{path, tag, node_ref, label}] re-applied after a re-export."""

    def __init__(self, targets, snapshot=None, pdf_path=None, source="output"):
        self.targets = [dict(t) for t in targets]
        self.snapshot = snapshot
        self.pdf_path = pdf_path
        # "output": tags of TagPilot's tagged PDF (re-applied after every export)
        # "input" : tags the original PDF already had (no tagged output yet);
        #           the pipeline replaces them, so they are never re-applied
        self.source = source

    @property
    def label(self):
        names = [t.get("label") or t.get("tag") or "?" for t in self.targets]
        return ", ".join(names[:3]) + (" ..." if len(names) > 3 else "")

    def apply(self, elements):
        return True

    def undo(self, elements):
        return True
