from django.apps import AppConfig

class AnalysisEngineConfig(AppConfig):
    name = 'analysis_engine'

    def ready(self):
        from .calibrate_analysis_db import calibrate
        # calibrate()  
        from accounts.addingdata import sync
        sync()