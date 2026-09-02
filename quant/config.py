"""
quant/config.py

Loads a strategy config YAML (see config/base.yaml, config/TEMPLATE.yaml)
into a plain dict. Kept deliberately tiny -- one function, no framework.
"""

from __future__ import annotations
import yaml


def load_config(path: str) -> dict:
    with open(path) as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError(f"{path} did not parse to a dict of parameters.")
    return cfg
