
from pathlib import Path
from functools import lru_cache

PROMPTS_DIR = Path(__file__).parent / "prompts"

@lru_cache(maxsize=128)
def _load_prompt_file(filepath):
    return filepath.read_text(encoding="utf-8").strip()

def get_analysis_prompt(club, model_type, monitoring_approach):
    club = club.upper()
    model_type = model_type.lower().replace(" ", "_")
    monitoring_approach = monitoring_approach.lower().replace(" ", "_")
    guardrails = _load_prompt_file(PROMPTS_DIR / "_base" / "guardrails.txt")
    club_gen = _load_prompt_file(PROMPTS_DIR / "_base" / f"{club.lower()}_generic.txt")
    model_type_p = _load_prompt_file(PROMPTS_DIR / "model_types" / f"{model_type}.txt")
    monitoring_p = _load_prompt_file(PROMPTS_DIR / "monitoring_approaches" / f"{monitoring_approach}.txt")
    return f"Analyze this report.\n\n{guardrails}\n\n{club_gen}\n\n{model_type_p}\n\n{monitoring_p}"

MODEL_REGISTRY = {
    "uptop_v3": {"club": "CL", "type": "risk_model", "monitoring": "risk_model"},
    "darwin": {"club": "CL", "type": "risk_model", "monitoring": "risk_model"}
}

def get_prompt_by_model_name(model_name):
    name = model_name.lower().replace(" ", "_")
    if name not in MODEL_REGISTRY:
        raise ValueError(f"Model {model_name} not found")
    c = MODEL_REGISTRY[name]
    return get_analysis_prompt(c["club"], c["type"], c["monitoring"])
