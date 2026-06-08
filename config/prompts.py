"""
Analysis Prompts
All AI prompts and prompt templates
"""

FRAUD_MONITORING_PROMPT = """Analyze this fraud monitoring report and provide a concise summary.

Format:

**Overall:** [Stable/Watch/At Risk] — [one-line reason]

**Top takeaways:**
1. [Key finding with numbers]
2. [Key finding with numbers]
3. [Key finding with numbers]
4. [Key finding with numbers]

**Positives:**
- [What went well]
- [What went well]

Focus on latest data, use actual numbers, keep under 200 words."""


def get_analysis_prompt(prompt_type="fraud_monitoring"):
    """
    Get prompt by type
    
    Args:
        prompt_type: Type of prompt to retrieve
        
    Returns:
        str: The prompt text
    """
    prompts = {
        "fraud_monitoring": FRAUD_MONITORING_PROMPT
    }
    return prompts.get(prompt_type, FRAUD_MONITORING_PROMPT)
