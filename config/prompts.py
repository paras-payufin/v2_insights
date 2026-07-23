from pathlib import Path
from functools import lru_cache

PROMPTS_DIR = Path(__file__).parent / "prompts"

@lru_cache(maxsize=128)
def _load_prompt_file(filepath):
    return filepath.read_text(encoding="utf-8").strip()

def get_analysis_prompt(
    club, model_type, model_specific, monitoring_approach, guardrails_file="guardrails"
):
    club = club.upper()
    model_type = model_type.lower().replace(" ", "_")
    monitoring_approach = monitoring_approach.lower().replace(" ", "_")

    model_specific_p = _load_prompt_file(PROMPTS_DIR / "model_specific" / f"{model_specific}.txt")
    guardrails = (
        _load_prompt_file(PROMPTS_DIR / "_base" / f"{guardrails_file}.txt")
        if guardrails_file
        else ""
    )

    # If the model-specific file is a complete self-contained prompt (starts with
    # a role definition), return it directly. Assembling it with the stub files
    # (guardrails, club context, model_type, monitoring) adds noise and causes
    # the model to echo the instruction fragments rather than produce analysis.
    if model_specific_p.lower().lstrip('*# \n').startswith(("you are", "role:")):
        return f"{guardrails}\n\n{model_specific_p}".strip() if guardrails else model_specific_p

    # Legacy assembly path — used for models that rely on the stub files.
    club_gen = _load_prompt_file(PROMPTS_DIR / "_base" / f"{club.lower()}_generic.txt")
    model_type_p = _load_prompt_file(PROMPTS_DIR / "model_types" / f"{model_type}.txt")
    monitoring_p = _load_prompt_file(PROMPTS_DIR / "monitoring_approaches" / f"{monitoring_approach}.txt")

    parts = ["Analyze this report."]
    if guardrails:
        parts.append(guardrails)
    parts.extend([club_gen, model_type_p, model_specific_p, monitoring_p])
    return "\n\n".join(parts)

MODEL_REGISTRY = {
    # CL Risk Models
    'uptop_v3': {
        'club': 'CL',
        'type': 'risk_model',
        'monitoring': 'risk_model',
        'model_specific': 'uptop_v3',
        'guardrails': None,  # self-contained prompt; no guardrails
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
    prompt = get_analysis_prompt(
        c["club"],
        c["type"],
        c["model_specific"],
        c["monitoring"],
        guardrails_file=c.get("guardrails", "guardrails"),
    )
    print(f"  [prompt] {model_name}: {len(prompt)} chars | "
          f"first 80: {prompt[:80]!r}")
    return prompt
