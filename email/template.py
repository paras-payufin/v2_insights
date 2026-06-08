"""
Email HTML Template Generator
Creates professional HTML emails with parsed sections
"""
import os
import re


def remove_emojis(text):
    """Remove emoji characters from text."""
    emoji_pattern = re.compile("["
        u"\U0001F600-\U0001F64F"
        u"\U0001F300-\U0001F5FF"
        u"\U0001F680-\U0001F6FF"
        u"\U0001F1E0-\U0001F1FF"
        u"\U00002702-\U000027B0"
        u"\U000024C2-\U0001F251"
        u"\U0001F900-\U0001F9FF"
        "]+", flags=re.UNICODE)
    return emoji_pattern.sub(r'', text)


def parse_analysis_sections(analysis_text):
    """
    Parse analysis text into structured sections
    
    Args:
        analysis_text: Raw analysis text from AI
        
    Returns:
        dict: Parsed sections (overall, takeaways, positives)
    """
    clean_analysis = remove_emojis(analysis_text)
    sections = {'overall': '', 'takeaways': [], 'positives': []}
    
    # Parse Overall section
    overall_match = re.search(r'\*\*Overall:\*\*\s*(.+?)(?=\n\n|\*\*Top|$)', 
                              clean_analysis, re.DOTALL | re.IGNORECASE)
    if overall_match:
        sections['overall'] = overall_match.group(1).strip()
    
    # Parse Takeaways
    takeaways_match = re.search(r'\*\*Top takeaways:\*\*\s*\n((?:\d+\..*?\n?)+)', 
                                clean_analysis, re.DOTALL | re.IGNORECASE)
    if takeaways_match:
        sections['takeaways'] = re.findall(r'\d+\.\s*(.+?)(?=\n\d+\.| \n\n|$)', 
                                          takeaways_match.group(1), re.DOTALL)
    
    # Parse Positives
    positives_match = re.search(r'\*\*Positives:\*\*\s*\n((?:[-•]\s*.+?\n?)+)', 
                                clean_analysis, re.DOTALL | re.IGNORECASE)
    if positives_match:
        sections['positives'] = re.findall(r'[-•]\s*(.+?)(?=\n[-•]|\n\n|$)', 
                                          positives_match.group(1), re.DOTALL)
    
    return sections


def determine_status(overall_text):
    """
    Determine status from overall text
    
    Args:
        overall_text: Overall section text
        
    Returns:
        dict: Status information (color, bg_color, status, icon)
    """
    overall_lower = overall_text.lower()
    
    if 'stable' in overall_lower:
        return {'color': '#28a745', 'bg_color': '#d4edda', 'status': 'STABLE', 'icon': '✓'}
    elif 'watch' in overall_lower:
        return {'color': '#ffc107', 'bg_color': '#fff3cd', 'status': 'WATCH', 'icon': '⚠'}
    elif 'risk' in overall_lower:
        return {'color': '#dc3545', 'bg_color': '#f8d7da', 'status': 'AT RISK', 'icon': '⚠'}
    else:
        return {'color': '#6c757d', 'bg_color': '#e9ecef', 'status': 'Unknown', 'icon': ''}


def build_takeaways_html(takeaways):
    """Build HTML for takeaways section."""
    html = ""
    for i, item in enumerate(takeaways, 1):
        clean_item = item.strip().replace('**', '')
        html += f"""
        <div style="display: table; width: 100%; margin-bottom: 16px; background-color: #f8f9fa; border-radius: 8px; padding: 16px; border-left: 4px solid #667eea;">
            <div style="display: table-cell; width: 36px; height: 36px; background-color: #667eea; color: white; text-align: center; vertical-align: middle; border-radius: 50%; font-weight: 600; font-size: 16px; line-height: 36px;">
                {i}
            </div>
            <div style="display: table-cell; padding-left: 16px; vertical-align: middle; color: #2d3748; font-size: 15px; line-height: 1.6;">
                {clean_item}
            </div>
        </div>"""
    return html


def build_positives_html(positives):
    """Build HTML for positives section."""
    html = ""
    for item in positives:
        clean_item = item.strip().replace('**', '')
        html += f"""
        <div style="margin-bottom: 12px; color: #2d3748; font-size: 15px; line-height: 1.6;">
            <span style="display: inline-block; width: 24px; height: 24px; background-color: #28a745; color: white; text-align: center; line-height: 24px; border-radius: 50%; margin-right: 10px; font-size: 14px; font-weight: bold;">✓</span>
            {clean_item}
        </div>"""
    return html
