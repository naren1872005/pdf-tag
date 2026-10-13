import os
import json
from gui.edit_actions import UpdateRoleAction

class PersistenceManager:
    VERSION = 1
    
    @staticmethod
    def get_manual_edits_path(project_path: str) -> str:
        return os.path.join(project_path, "semantic", "manual_edits.json")
        
    @staticmethod
    def save_edits(project_path: str, actions: list):
        """
        Saves a list of UpdateRoleAction objects to manual_edits.json.
        """
        path = PersistenceManager.get_manual_edits_path(project_path)
        
        from gui.edit_actions import UpdateRoleAction, UpdateBBoxAction, RemoveTagAction, CreateTagAction, UpdateTextAction
        
        serialized_actions = []
        for action in actions:
            if action.__class__.__name__ == 'UpdateRoleAction':
                serialized_actions.append({
                    "action": "UPDATE_ROLE",
                    "node_id": action.node_id,
                    "old_role": action.old_role,
                    "new_role": action.new_role
                })
            elif action.__class__.__name__ == 'UpdateBBoxAction':
                serialized_actions.append({
                    "action": "UPDATE_BBOX",
                    "node_id": action.node_id,
                    "old_bbox": action.old_bbox,
                    "new_bbox": action.new_bbox
                })
            elif action.__class__.__name__ == 'RemoveTagAction':
                serialized_actions.append({
                    "action": "REMOVE_TAG",
                    "node_id": action.node_id,
                    "node_dict": action.node_dict
                })
            elif action.__class__.__name__ == 'CreateTagAction':
                serialized_actions.append({
                    "action": "CREATE_TAG",
                    "node_id": action.node_id,
                    "node_dict": action.node_dict
                })
            elif action.__class__.__name__ == 'BatchRemoveTagAction':
                serialized_actions.append({
                    "action": "BATCH_REMOVE_TAG",
                    "node_dicts": action.node_dicts
                })
            elif action.__class__.__name__ == 'DeleteStructTagsAction':
                serialized_actions.append({
                    "action": "DELETE_STRUCT_TAGS",
                    "targets": action.targets,
                    "source": getattr(action, "source", "output")
                })
            elif action.__class__.__name__ == 'UpdateTextAction':
                serialized_actions.append({
                    "action": "UPDATE_TEXT",
                    "node_id": action.node_id,
                    "old_text": action.old_text,
                    "new_text": action.new_text
                })
                
        data = {
            "version": PersistenceManager.VERSION,
            "actions": serialized_actions
        }
        
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
            
    @staticmethod
    def load_edits(project_path: str, logger=print) -> list:
        from gui.edit_actions import UpdateRoleAction, UpdateBBoxAction, RemoveTagAction, CreateTagAction, UpdateTextAction
        path = PersistenceManager.get_manual_edits_path(project_path)
        if not os.path.exists(path):
            return []
            
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            logger(f"Warning: Failed to read {path}. Assuming no manual edits. Error: {e}")
            return []
            
        if data.get("version") != PersistenceManager.VERSION:
            logger(f"Warning: Unrecognized version in {path}. Trying anyway.")
            
        actions = []
        for action_data in data.get("actions", []):
            atype = action_data.get("action")
            if atype == "UPDATE_ROLE":
                actions.append(UpdateRoleAction(action_data["node_id"], action_data["old_role"], action_data["new_role"]))
            elif atype == "UPDATE_BBOX":
                actions.append(UpdateBBoxAction(action_data["node_id"], action_data["old_bbox"], action_data["new_bbox"]))
            elif atype == "REMOVE_TAG":
                actions.append(RemoveTagAction(action_data["node_id"], action_data["node_dict"]))
            elif atype == "CREATE_TAG":
                actions.append(CreateTagAction(action_data["node_id"], action_data["node_dict"]))
            elif atype == "BATCH_REMOVE_TAG":
                from gui.edit_actions import BatchRemoveTagAction
                actions.append(BatchRemoveTagAction(action_data.get("node_dicts") or []))
            elif atype == "DELETE_STRUCT_TAGS":
                from gui.edit_actions import DeleteStructTagsAction
                actions.append(DeleteStructTagsAction(action_data.get("targets") or [],
                                                      source=action_data.get("source") or "output"))
            elif atype == "UPDATE_TEXT":
                actions.append(UpdateTextAction(action_data["node_id"], action_data["new_text"], action_data["old_text"]))
        return actions
