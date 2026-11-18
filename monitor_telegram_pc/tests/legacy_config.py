"""Configuração histórica explícita para testes das políticas anteriores."""
from pathlib import Path
from settings import load_config as actual_load_config

def load_config(path=None):
    return actual_load_config(path or Path(__file__).with_name('config_v3.json'))
