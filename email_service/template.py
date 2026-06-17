"""
Email HTML Template Generator - Professional Premium Edition
Creates luxury, minimalistic HTML emails for any analysis type
"""
import re
from datetime import datetime
from utils.helpers import remove_emojis


# ====================================================================
# CONFIGURATION - Customize per model/use case
# ====================================================================

EMAIL_CONFIG = {
    'fraud': {
        'title': 'Fraud Monitoring Report',
        'subtitle': 'Comprehensive Risk Analysis',
        'primary_color': '#1a1a2e',
        'accent_color': '#0f4c75',
        'sections_to_extract': ['Overall', 'Top takeaways', 'Positives', 'Key findings', 'Recommendations']
    },
    'financial': {
        'title': 'Financial Analysis Report',
        'subtitle': 'Executive Insights',
        'primary_color': '#1a3a52',
        'accent_color': '#2e5266',
        'sections_to_extract': ['Executive Summary', 'Key metrics', 'Findings', 'Recommendations']
    },
    'operational': {
        'title': 'Operational Intelligence Report',
        'subtitle': 'Performance Analysis',
        'primary_color': '#2c3e50',
        'accent_color': '#34495e',
        'sections_to_extract': ['Summary', 'Key insights', 'Performance indicators', 'Action items']
    },
    'default': {
        'title': 'Analysis Report',
        'subtitle': 'Comprehensive Insights',
        'primary_color': '#2c3e50',
        'accent_color': '#34495e',
        'sections_to_extract': ['Summary', 'Key points', 'Findings', 'Insights', 'Recommendations']
    }
}


def get_config(model_name='default'):
    """Get email configuration for specific model"""
    return EMAIL_CONFIG.get(model_name.lower(), EMAIL_CONFIG['default'])


# ====================================================================
# INTELLIGENT PARSING - Works with any content structure
# ====================================================================

def extract_sections_intelligently(text):
    """
    Intelligently extract sections from ANY formatted text
    Returns a list of {'title': str, 'content': str, 'items': list}
    """
    clean_text = remove_emojis(text)
    sections = []
    
    # Pattern 1: Bold headers with content (most common)
    # Matches: **Title:** or **Title** followed by content
    bold_sections = re.finditer(
        r'\*\*([^*]+?)(?::|：)?\*\*\s*\n+(.*?)(?=\n\*\*|\n#{1,6}\s|\Z)',
        clean_text,
        re.DOTALL | re.IGNORECASE
    )
    
    for match in bold_sections:
        title = match.group(1).strip()
        content = match.group(2).strip()
        
        # Extract list items if present
        items = []
        
        # Numbered lists: 1. 2. 3.
        numbered = re.findall(r'^\s*\d+\.\s*(.+?)(?=\n\s*\d+\.|\n\n|\Z)', content, re.MULTILINE | re.DOTALL)
        if numbered:
            items = [item.strip().replace('**', '').replace('\n', ' ') for item in numbered]
        
        # Bullet lists: - or • or *
        if not items:
            bullets = re.findall(r'^\s*[-•*]\s*(.+?)(?=\n\s*[-•*]|\n\n|\Z)', content, re.MULTILINE | re.DOTALL)
            if bullets:
                items = [item.strip().replace('**', '').replace('\n', ' ') for item in bullets]
        
        # If no lists found, treat as paragraph
        if not items:
            # Remove list markers and clean up
            clean_content = re.sub(r'^\s*[-•*\d]+\.\s*', '', content, flags=re.MULTILINE)
            items = [p.strip() for p in clean_content.split('\n\n') if p.strip()]
        
        sections.append({
            'title': title,
            'content': content,
            'items': items,
            'is_list': bool(numbered or bullets)
        })
    
    # Pattern 2: Markdown headers (## Title)
    if not sections:
        md_sections = re.finditer(
            r'^#{1,6}\s+(.+?)\n+(.*?)(?=\n#{1,6}\s|\Z)',
            clean_text,
            re.MULTILINE | re.DOTALL
        )
        for match in md_sections:
            title = match.group(1).strip()
            content = match.group(2).strip()
            sections.append({
                'title': title,
                'content': content,
                'items': [content],
                'is_list': False
            })
    
    # Fallback: Split by double newlines if no structure found
    if not sections:
        paragraphs = [p.strip() for p in clean_text.split('\n\n') if p.strip()]
        if paragraphs:
            sections.append({
                'title': 'Analysis',
                'content': '\n\n'.join(paragraphs),
                'items': paragraphs,
                'is_list': False
            })
    
    return sections


def prioritize_sections(sections, config):
    """
    Organize sections by priority based on config keywords
    Returns: {'primary': section, 'key_points': [sections], 'additional': [sections]}
    """
    result = {'primary': None, 'key_points': [], 'additional': []}
    
    # Get keywords from config (convert to lowercase)
    priority_keywords = [kw.lower() for kw in config.get('sections_to_extract', [])]
    
    # If no config provided, use defaults
    if not priority_keywords:
        priority_keywords = ['overall', 'summary', 'executive', 'finding', 'insight', 'recommendation']
    
    # Define what counts as "primary" (summary/overview sections)
    primary_keywords = ['overall', 'summary', 'executive summary', 'executive', 'conclusion', 'overview']
    
    # Define what counts as "key points" (insights/findings/recommendations)
    keypoint_keywords = ['takeaway', 'finding', 'insight', 'key', 'recommendation', 'action', 'metric', 'positive']
    
    for section in sections:
        title_lower = section['title'].lower()
        
        # Check if this section matches any config keyword
        matches_config = any(keyword in title_lower for keyword in priority_keywords)
        
        if not matches_config:
            # If not in config, add to additional sections (will be shown but lower priority)
            result['additional'].append(section)
            continue
        
        # Matched config! Now categorize it:
        
        # Primary section (summary/overall/executive summary)
        if any(kw in title_lower for kw in primary_keywords):
            if not result['primary']:
                result['primary'] = section
            else:
                result['key_points'].append(section)
        
        # Key points (findings/insights/recommendations)
        elif any(kw in title_lower for kw in keypoint_keywords):
            result['key_points'].append(section)
        
        # Matched config but not specifically categorized - add to key points
        else:
            result['key_points'].append(section)
    
    return result


def determine_status_intelligent(text):
    """
    Intelligently determine status from content
    More sophisticated than keyword matching
    """
    text_lower = text.lower()
    
    # Risk indicators
    risk_words = ['critical', 'urgent', 'severe', 'high risk', 'danger', 'alert', 'warning', 'breach']
    risk_score = sum(1 for word in risk_words if word in text_lower)
    
    # Positive indicators
    positive_words = ['stable', 'healthy', 'good', 'normal', 'secure', 'compliant', 'low risk']
    positive_score = sum(1 for word in positive_words if word in text_lower)
    
    # Caution indicators
    caution_words = ['watch', 'monitor', 'attention', 'review', 'moderate', 'medium']
    caution_score = sum(1 for word in caution_words if word in text_lower)
    
    # Determine status based on scores
    if risk_score >= 2:
        return {'color': '#c0392b', 'bg_color': '#fadbd8', 'status': 'REQUIRES ATTENTION', 'icon': '●'}
    elif caution_score >= 2 or (risk_score == 1 and positive_score == 0):
        return {'color': '#d68910', 'bg_color': '#fcf3cf', 'status': 'MONITOR', 'icon': '●'}
    elif positive_score >= 2:
        return {'color': '#27ae60', 'bg_color': '#d5f4e6', 'status': 'NORMAL', 'icon': '●'}
    else:
        return {'color': '#5d6d7e', 'bg_color': '#eaecee', 'status': 'REVIEWED', 'icon': '●'}


# ====================================================================
# HTML BUILDERS - Premium minimalistic design
# ====================================================================

def build_section_html(section, index, total_sections, config):
    """Build HTML for a single section with premium styling"""
    
    if section['is_list'] and len(section['items']) > 1:
        # List-based section
        items_html = ""
        for i, item in enumerate(section['items'], 1):
            items_html += f"""
            <tr>
                <td style="padding: 16px 24px; border-bottom: 1px solid #ecf0f1;">
                    <table width="100%" cellpadding="0" cellspacing="0">
                        <tr>
                            <td width="32" style="vertical-align: top; padding-top: 2px;">
                                <div style="width: 24px; height: 24px; background-color: {config['accent_color']}; color: #ffffff; border-radius: 4px; text-align: center; line-height: 24px; font-size: 12px; font-weight: 600;">{i}</div>
                            </td>
                            <td style="padding-left: 16px; color: #2c3e50; font-size: 14px; line-height: 1.7; font-weight: 400;">
                                {item}
                            </td>
                        </tr>
                    </table>
                </td>
            </tr>"""
        
        return f"""
        <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom: 32px; background-color: #ffffff; border-radius: 8px; overflow: hidden; border: 1px solid #ecf0f1;">
            <tr>
                <td style="background-color: #f8f9fa; padding: 16px 24px; border-bottom: 2px solid {config['accent_color']};">
                    <h2 style="margin: 0; color: {config['primary_color']}; font-size: 16px; font-weight: 600; letter-spacing: 0.3px; text-transform: uppercase;">{section['title']}</h2>
                </td>
            </tr>
            {items_html}
        </table>"""
    
    else:
        # Paragraph-based section
        content_html = "<br><br>".join(section['items'])
        return f"""
        <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom: 32px;">
            <tr>
                <td style="padding-bottom: 12px;">
                    <h2 style="margin: 0; color: {config['primary_color']}; font-size: 16px; font-weight: 600; letter-spacing: 0.3px; text-transform: uppercase; border-bottom: 2px solid {config['accent_color']}; padding-bottom: 8px;">{section['title']}</h2>
                </td>
            </tr>
            <tr>
                <td style="padding: 20px 24px; background-color: #f8f9fa; border-radius: 8px; border-left: 4px solid {config['accent_color']};">
                    <p style="margin: 0; color: #2c3e50; font-size: 14px; line-height: 1.7; font-weight: 400;">
                        {content_html}
                    </p>
                </td>
            </tr>
        </table>"""


def create_html_email(file_name, analysis_text, model_name='default'):
    """
    Create professional, premium HTML email from analysis
    Works for ANY model and content structure
    
    Args:
        file_name: Name of analyzed file
        analysis_text: Raw analysis text
        model_name: Model type for configuration (default/fraud/financial/etc.)
        
    Returns:
        str: Complete HTML email
    """
    # Get configuration
    config = get_config(model_name)
    
    # Extract and organize sections
    all_sections = extract_sections_intelligently(analysis_text)
    organized = prioritize_sections(all_sections, config)  # ✅ FIXED: Pass config instead of config['sections_to_extract']
    
    # Determine status from primary section or full text
    status_text = organized['primary']['content'] if organized['primary'] else analysis_text[:500]
    status = determine_status_intelligent(status_text)
    
    # Build primary section (executive summary)
    primary_html = ""
    if organized['primary']:
        primary_content = "<br><br>".join(organized['primary']['items'])
        primary_html = f"""
        <tr>
            <td style="padding: 0 48px 32px 48px;">
                <table width="100%" cellpadding="0" cellspacing="0" style="background-color: {status['bg_color']}; border-radius: 8px; overflow: hidden; border-left: 4px solid {status['color']};">
                    <tr>
                        <td style="padding: 24px 28px;">
                            <table width="100%" cellpadding="0" cellspacing="0">
                                <tr>
                                    <td width="40" style="vertical-align: middle;">
                                        <div style="width: 32px; height: 32px; background-color: {status['color']}; border-radius: 50%; text-align: center; line-height: 32px; color: #ffffff; font-size: 14px; font-weight: 600;">{status['icon']}</div>
                                    </td>
                                    <td style="padding-left: 16px; vertical-align: middle;">
                                        <div style="color: {status['color']}; font-size: 13px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase;">{status['status']}</div>
                                    </td>
                                </tr>
                            </table>
                            <div style="margin-top: 16px; color: #2c3e50; font-size: 14px; line-height: 1.7; font-weight: 400;">
                                {primary_content}
                            </div>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>"""
    
    # Build key sections
    key_sections_html = ""
    for i, section in enumerate(organized['key_points']):
        key_sections_html += f"""
        <tr>
            <td style="padding: 0 48px;">
                {build_section_html(section, i, len(organized['key_points']), config)}
            </td>
        </tr>"""
    
    # Build additional sections
    additional_html = ""
    for i, section in enumerate(organized['additional']):
        additional_html += f"""
        <tr>
            <td style="padding: 0 48px;">
                {build_section_html(section, i, len(organized['additional']), config)}
            </td>
        </tr>"""
    
    # Generate timestamp
    timestamp = datetime.now().strftime('%B %d, %Y at %H:%M UTC')
    
    # Build complete email
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="X-UA-Compatible" content="IE=edge">
    <title>{config['title']}</title>
</head>
<body style="margin: 0; padding: 0; background-color: #ecf0f1; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale;">
    
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #ecf0f1; padding: 48px 0;">
        <tr>
            <td align="center">
                
                <!-- Main Container -->
                <table width="680" cellpadding="0" cellspacing="0" border="0" style="background-color: #ffffff; border-radius: 12px; box-shadow: 0 8px 24px rgba(0,0,0,0.08); overflow: hidden;">
                    
                    <!-- Header -->
                    <tr>
                        <td style="background: linear-gradient(135deg, {config['primary_color']} 0%, {config['accent_color']} 100%); padding: 48px 48px 40px 48px; text-align: center;">
                            <h1 style="color: #ffffff; margin: 0 0 8px 0; font-size: 26px; font-weight: 700; letter-spacing: -0.5px;">{config['title']}</h1>
                            <p style="color: rgba(255,255,255,0.85); margin: 0 0 16px 0; font-size: 13px; font-weight: 500; letter-spacing: 0.5px; text-transform: uppercase;">{config['subtitle']}</p>
                            <div style="background-color: rgba(255,255,255,0.15); padding: 12px 20px; border-radius: 6px; display: inline-block;">
                                <p style="color: #ffffff; margin: 0; font-size: 13px; font-weight: 500;">{file_name}</p>
                            </div>
                        </td>
                    </tr>
                    
                    <!-- Spacer -->
                    <tr>
                        <td style="height: 40px;"></td>
                    </tr>
                    
                    <!-- Primary Section (Status/Summary) -->
                    {primary_html}
                    
                    <!-- Key Sections -->
                    {key_sections_html}
                    
                    <!-- Additional Sections -->
                    {additional_html}
                    
                    <!-- Spacer -->
                    <tr>
                        <td style="height: 24px;"></td>
                    </tr>
                    
                    <!-- Footer -->
                    <tr>
                        <td style="background-color: #f8f9fa; padding: 32px 48px; text-align: center; border-top: 1px solid #ecf0f1;">
                            <p style="color: #7f8c8d; font-size: 12px; margin: 0 0 4px 0; font-weight: 500;">Generated by Toqan Analysis Platform</p>
                            <p style="color: #95a5a6; font-size: 11px; margin: 0; font-weight: 400;">{timestamp}</p>
                        </td>
                    </tr>
                    
                </table>
                
            </td>
        </tr>
    </table>
    
</body>
</html>"""
    
    return html
