import json
import os

class DataManager:
    """Handles all persistent storage operations for modularity."""
    def __init__(self, configs_file="bottle_configs.json", history_file="session_history.json"):
        self.configs_file = configs_file
        self.history_file = history_file

    def load_bottle_configs(self):
        if os.path.exists(self.configs_file):
            try:
                with open(self.configs_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[DATA MANAGER] Error reading configs: {e}")
                
        # Default fallback configurations
        return {
            "500ml_Kik Cola": 300,
            "500ml_Lemonade": 280,
            "500ml_Necto": 290,
            "1l_Orange Crush": 350,
            "1.5l_Cream Soda": 400
        }

    def save_bottle_configs(self, configs):
        try:
            with open(self.configs_file, "w") as f:
                json.dump(configs, f, indent=4)
        except Exception as e:
            print(f"[DATA MANAGER] Error saving bottle configs: {e}")

    def load_history(self):
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[DATA MANAGER] Error reading history: {e}")
                return []
        return []

    def save_history(self, history):
        try:
            with open(self.history_file, "w") as f:
                json.dump(history, f, indent=4)
        except Exception as e:
            print(f"[DATA MANAGER] Error saving history: {e}")

