from pathlib import Path
from functools import lru_cache

PROMPTS_DIR = Path(__file__).parent / "prompts"

@lru_cache(maxsize=128)
def _load_prompt_file(filepath):
    return filepath.read_text(encoding="utf-8").strip()

def get_analysis_prompt(club, model_type, model_specific, monitoring_approach):
    club = club.upper()
    model_type = model_type.lower().replace(" ", "_")
    monitoring_approach = monitoring_approach.lower().replace(" ", "_")
    
    guardrails = _load_prompt_file(PROMPTS_DIR / "_base" / "guardrails.txt")
    club_gen = _load_prompt_file(PROMPTS_DIR / "_base" / f"{club.lower()}_generic.txt")
    model_type_p = _load_prompt_file(PROMPTS_DIR / "model_types" / f"{model_type}.txt")
    model_specific_p = _load_prompt_file(PROMPTS_DIR / "model_specific" / f"{model_specific}.txt")
    monitoring_p = _load_prompt_file(PROMPTS_DIR / "monitoring_approaches" / f"{monitoring_approach}.txt")
    
    return f"Analyze this report.\n\n{guardrails}\n\n{club_gen}\n\n{model_type_p}\n\n{model_specific_p}\n\n{monitoring_p}"

MODEL_REGISTRY = {
    # CL Risk Models
    'uptop_v3': {
        'club': 'CL',
        'type': 'risk_model',
        'monitoring': 'risk_model',
        'model_specific': 'uptop_v3'
    },
    'uptop_v3_with_ri': {
        'club': 'CL',
        'type': 'risk_model',
        'monitoring': 'risk_model',
        'model_specific': 'uptop_v3_with_ri'
    },
    'darwin': {
        'club': 'CL',
        'type': 'risk_model',
        'monitoring': 'risk_model',
        'model_specific': 'darwin'
    },
    'uptop_v2': {
        'club': 'CL',
        'type': 'risk_model',
        'monitoring': 'multiple',
        'model_specific': 'uptop_v2'
    },
    
    # CL Collection Models
    'pre_due': {
        'club': 'CL',
        'type': 'collection',
        'monitoring': 'pre_due',
        'model_specific': 'pre_due'
    },
    'cam': {
        'club': 'CL',
        'type': 'collection',
        'monitoring': 'cam',
        'model_specific': 'cam'
    },
    'write_off': {
        'club': 'CL',
        'type': 'collection',
        'monitoring': 'write_off',
        'model_specific': 'write_off'
    },
    
    # CL Underwriting
    'bureau_depth': {
        'club': 'CL',
        'type': 'underwriting',
        'monitoring': 'application_stage',
        'model_specific': 'bureau_depth'
    },
    
    # TC TCN Fraud
    'rcm': {
        'club': 'TC',
        'type': 'tcn_fraud',
        'monitoring': 'realtime',
        'model_specific': 'rcm'
    },
    
    # TC Collection
    'ice': {
        'club': 'TC',
        'type': 'collection',
        'monitoring': 'application_stage',
        'model_specific': 'ice'
    },
    
    # TC Utility
    'fraud': {
        'club': 'TC',
        'type': 'utility',
        'monitoring': 'realtime',
        'model_specific': 'fraud_specific'
    },
    
    # TC Repeat
    'affluence': {
        'club': 'TC',
        'type': 'repeat',
        'monitoring': 'multiple',
        'model_specific': 'affluence'
    },
    
    # TC New User
    'name_match': {
        'club': 'TC',
        'type': 'new_user',
        'monitoring': 'application_stage',
        'model_specific': 'name_match'
    }
}


def get_prompt_by_model_name(model_name):
    name = model_name.lower().replace(" ", "_")
    if name not in MODEL_REGISTRY:
        raise ValueError(f"Model {model_name} not found")
    c = MODEL_REGISTRY[name]
    return get_analysis_prompt(c["club"], c["type"], c["model_specific"], c["monitoring"])
