from django.apps import AppConfig

class AnalysisEngineConfig(AppConfig):
    name = 'analysis_engine'

    def ready(self):
        try:
            from .calibrate_analysis_db import calibrate
            calibrate()
        except Exception as e:
            print(f"calibrate error: {e}")
        
        try:
            from accounts.addingdata import sync
            sync()
        except Exception as e:
            print(f"sync error: {e}")