import json
import os

class DataManager:
    def __init__(self, config_path="bottle_configs.json", preview_dir="assets/previews"):
        self.config_path = config_path
        self.preview_dir = preview_dir
        
        # Ensure the preview directory exists
        if not os.path.exists(self.preview_dir):
            os.makedirs(self.preview_dir)
            
        self.configs = self.load_configs()

    def load_configs(self):
        """Loads the recipe library from disk."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"[DATA MANAGER] Error reading configs: {e}")
        return {}

    def get_recipe(self, bottle_key):
        """
        Retrieves settings for a specific bottle.
        Default settings provided if key not found to prevent crashes.
        """
        return self.configs.get(bottle_key, {
            "threshold": 300, 
            "exposure_us": 240, 
            "preview_path": None
        })

    def save_recipe(self, bottle_key, threshold, exposure, preview_path):
        """Updates a recipe and saves it safely to the JSON file."""
        self.configs[bottle_key] = {
            "threshold": threshold,
            "exposure_us": exposure,
            "preview_path": preview_path
        }
        try:
            with open(self.config_path, 'w') as f:
                json.dump(self.configs, f, indent=4)
        except Exception as e:
            print(f"[DATA MANAGER] Error saving recipe: {e}")

    def load_history(self):
        """Loads the session history from disk for the GUI."""
        history_file = "session_history.json"
        if os.path.exists(history_file):
            try:
                with open(history_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"[DATA MANAGER] Error reading history: {e}")
                return []
        return []

    def save_session_log(self, session_data):
        """Appends session data to session_history.json, keeping only the last 100."""
        history_file = "session_history.json"
        history = self.load_history()
        
        history.insert(0, session_data) # Keep latest at the top
        history = history[:100]         # Cap at 100 entries to save RAM
        
        try:
            with open(history_file, 'w') as f:
                json.dump(history, f, indent=4)
        except Exception as e:
            print(f"[DATA MANAGER] Error saving session log: {e}")
