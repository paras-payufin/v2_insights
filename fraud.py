#!/usr/bin/env python3
"""
Toqan Analysis DAG - Production Pipeline
Main orchestration file for fraud monitoring report analysis
"""
import boto3
import time
from io import BytesIO
from datetime import datetime

# Import from local modules
from config.settings import (
    S3_BUCKET, S3_FOLDER, SUPPORTED_EXTENSIONS,
)

from utils.utils import (
    TOQAN_API_KEY, TOQAN_BASE_URL, RECIPIENT_EMAIL
)

from config.prompts import get_prompt_by_model_name
from email.template import create_html_email , EMAIL_CONFIG 
from email.sender import send_email
from utils.helpers import make_api_request, get_analysis, remove_emojis


def find_latest_file():
    """
    Find latest file in S3 bucket
    
    Returns:
        tuple: (s3_key, file_name, file_size)
    """
    print(f"\n📂 Finding latest file in s3://{S3_BUCKET}/{S3_FOLDER}")
    s3 = boto3.client('s3')
    response = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=S3_FOLDER)
    
    files = [obj for obj in response.get('Contents', []) 
             if obj['Key'].endswith(SUPPORTED_EXTENSIONS)]
    
    if not files:
        raise Exception("No supported files found in S3")
    
    latest = max(files, key=lambda x: x['LastModified'])
    s3_key = latest['Key']
    file_name = s3_key.split('/')[-1]
    file_size = latest['Size'] / 1024 / 1024  # Convert to MB
    
    print(f"✓ Found: {file_name} ({file_size:.2f} MB)")
    return s3_key, file_name, file_size


def download_file(s3_key):
    """
    Download file from S3 to memory buffer
    
    Args:
        s3_key: S3 object key
        
    Returns:
        BytesIO: File buffer
    """
    print(f"\n⬇️  Downloading file from S3...")
    s3 = boto3.client('s3')
    buffer = BytesIO()
    s3.download_fileobj(Bucket=S3_BUCKET, Key=s3_key, Fileobj=buffer)
    buffer.seek(0)
    print(f"✓ Download complete")
    return buffer


def upload_to_toqan(file_name, file_buffer):
    """
    Upload file to Toqan API
    
    Args:
        file_name: Name of the file
        file_buffer: File content buffer
        
    Returns:
        str: Toqan file ID
    """
    print(f"\n☁️  Uploading to Toqan...")
    time.sleep(3)  # Rate limit protection
    
    headers = {"X-Api-Key": TOQAN_API_KEY, "accept": "application/json"}
    files_payload = {'file': (file_name, file_buffer, 'application/octet-stream')}
    
    upload_response = make_api_request(
        'PUT', f"{TOQAN_BASE_URL}/upload_file", headers, files=files_payload
    )
    
    file_id = upload_response.json()['file_id']
    print(f"✓ Upload complete (ID: {file_id})")
    return file_id


MODEL_NAME = 'fraud'


def create_analysis_conversation(file_id):
    """
    Create analysis conversation with Toqan
    
    Args:
        file_id: Toqan file ID
        
    Returns:
        str: Conversation ID
    """
    print(f"\n🤖 Creating analysis conversation for {MODEL_NAME}...")
    time.sleep(5)
    
    headers = {"X-Api-Key": TOQAN_API_KEY, "accept": "application/json"}
    prompt = get_prompt_by_model_name(MODEL_NAME)
    
    print(f"✓ Loaded prompt for {MODEL_NAME} (length: {len(prompt)} chars)")
    
    conversation_data = {
        "user_message": prompt,
        "private_user_files": [{"ID": file_id}]
    }
    
    conv_response = make_api_request(
        'POST', f"{TOQAN_BASE_URL}/create_conversation", headers, json_data=conversation_data
    )
    
    conversation_id = conv_response.json()['conversation_id']
    print(f"✓ Conversation created (ID: {conversation_id})")
    return conversation_id


def wait_for_analysis(conversation_id):
    """
    Wait for and retrieve analysis from Toqan
    
    Args:
        conversation_id: Toqan conversation ID
        
    Returns:
        str: Analysis text
    """
    print(f"\n⏳ Waiting for analysis (2-5 min)...")
    time.sleep(30)  # Initial wait
    
    headers = {"X-Api-Key": TOQAN_API_KEY, "accept": "application/json"}
    analysis = get_analysis(conversation_id, headers, TOQAN_BASE_URL)
    
    if not analysis:
        raise Exception("No analysis received from Toqan")
    
    return analysis


def send_report_email(file_name, analysis, model_name='fraud'):  # ✅ Add model_name parameter
    """
    Generate and send email report
    
    Args:
        file_name: Name of analyzed file
        analysis: Analysis text from Toqan
        model_name: Model type for email configuration
    """
    print(f"\n📧 Creating and sending email...")
    
    html_email = create_html_email(file_name, analysis, model_name)  # ✅ Pass model_name
    text_email = remove_emojis(analysis)
    
    subject = f"{EMAIL_CONFIG.get(model_name, EMAIL_CONFIG['default'])['title']} - {file_name} - {datetime.now().strftime('%Y-%m-%d')}"
    
    send_email(
        subject=subject,
        html_content=html_email,
        text_content=text_email,
        recipients=RECIPIENT_EMAIL
    )



def main():
    """Main pipeline execution"""
    print("=" * 80)
    print("🚀 TOQAN ANALYSIS PIPELINE - PRODUCTION")
    print("=" * 80)
    
    buffer = None
    
    try:
        # Step 1: Find latest file
        s3_key, file_name, file_size = find_latest_file()
        
        # Step 2: Download file
        buffer = download_file(s3_key)
        
        # Step 3: Upload to Toqan
        file_id = upload_to_toqan(file_name, buffer)
        
        # Step 4: Create analysis conversation
        conversation_id = create_analysis_conversation(file_id)
        
        # Step 5: Wait for analysis
        analysis = wait_for_analysis(conversation_id)
        
        # Step 6: Send email report
        send_report_email(file_name, analysis, MODEL_NAME)  # ✅ Pass MODEL_NAME
        
        print("\n" + "=" * 80)
        print("✅ SUCCESS - Pipeline completed!")
        print("=" * 80)
        
    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        raise
        
    finally:
        if buffer:
            buffer.close()


if __name__ == "__main__":
    main()