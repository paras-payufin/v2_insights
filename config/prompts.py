from pathlib import Path
from functools import lru_cache

PROMPTS_DIR = Path(__file__).parent / "prompts"

@lru_cache(maxsize=128)
def _load_prompt_file(filepath):
    return filepath.read_text(encoding="utf-8").strip()

def _apply_prompt_vars(prompt, prompt_vars):
    """
    Replace {{KEY}} markers in a prompt with dynamic, per-run values (e.g. the
    exact category labels found in this run's data file). Any marker left
    without a supplied value is left as a visible placeholder rather than
    silently dropped, so a missing wiring bug is obvious instead of producing
    a subtly broken prompt.
    """
    if not prompt_vars:
        return prompt
    for key, value in prompt_vars.items():
        prompt = prompt.replace("{{" + key + "}}", value if value else "(none found)")
    return prompt


def get_analysis_prompt(club, model_type, model_specific, monitoring_approach, prompt_vars=None):
    club = club.upper()
    model_type = model_type.lower().replace(" ", "_")
    monitoring_approach = monitoring_approach.lower().replace(" ", "_")

    model_specific_p = _load_prompt_file(PROMPTS_DIR / "model_specific" / f"{model_specific}.txt")

    # If the model-specific file is a complete self-contained prompt (starts with
    # a role definition), return it directly. Assembling it with the stub files
    # (guardrails, club context, model_type, monitoring) adds noise and causes
    # the model to echo the instruction fragments rather than produce analysis.
    if model_specific_p.lower().lstrip('*# \n').startswith(("you are", "role:")):
        guardrails = _load_prompt_file(PROMPTS_DIR / "_base" / "guardrails.txt")
        return _apply_prompt_vars(f"{guardrails}\n\n{model_specific_p}", prompt_vars)

    # Legacy assembly path — used for models that rely on the stub files.
    guardrails = _load_prompt_file(PROMPTS_DIR / "_base" / "guardrails.txt")
    club_gen = _load_prompt_file(PROMPTS_DIR / "_base" / f"{club.lower()}_generic.txt")
    model_type_p = _load_prompt_file(PROMPTS_DIR / "model_types" / f"{model_type}.txt")
    monitoring_p = _load_prompt_file(PROMPTS_DIR / "monitoring_approaches" / f"{monitoring_approach}.txt")

    prompt = f"Analyze this report.\n\n{guardrails}\n\n{club_gen}\n\n{model_type_p}\n\n{model_specific_p}\n\n{monitoring_p}"
    return _apply_prompt_vars(prompt, prompt_vars)

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


def get_narrative_prompt(facts_json, model_specific="uptop_v3_narrative"):
    """
    Load the narrative-only prompt and inject the pre-computed facts JSON.

    Unlike get_analysis_prompt(), this deliberately bypasses guardrails/club/
    model-type assembly: the narrative prompt is self-contained on purpose —
    it is not asking the model to produce a full report, only prose strings
    around numbers that are already final, so none of the "read the file,
    render every section, use exact labels" machinery built for full-report
    generation is relevant here.
    """
    prompt = _load_prompt_file(PROMPTS_DIR / "model_specific" / f"{model_specific}.txt")
    return _apply_prompt_vars(prompt, {"FACTS_JSON": facts_json})


def get_prompt_by_model_name(model_name, prompt_vars=None):
    name = model_name.lower().replace(" ", "_")
    if name not in MODEL_REGISTRY:
        raise ValueError(f"Model {model_name} not found")
    c = MODEL_REGISTRY[name]
    prompt = get_analysis_prompt(
        c["club"], c["type"], c["model_specific"], c["monitoring"], prompt_vars=prompt_vars
    )
    print(f"  [prompt] {model_name}: {len(prompt)} chars | "
          f"first 80: {prompt[:80]!r}")
    return prompt
