import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from email.header import Header
from flask import Flask, request, render_template, flash, redirect, url_for, jsonify
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

import threading

# Load environment variables from .env file
load_dotenv()

from transaction_processor import process_transactions

import json

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "supersecretkey") # Replace with a strong secret key
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB max upload size

# Ensure the upload folder exists
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# Email configuration from environment variables
SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587)) # Default to 587 for TLS
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")

def process_and_email_task(app, input_filepath, output_filepath, email):
    with app.app_context():
        try:
            # Process the transactions
            processed_file_path = process_transactions(input_filepath, output_filepath)

            # Send email with the processed file
            send_email(
                recipient_email=email,
                subject="Your Categorized Transactions Report",
                body="Please find your categorized transactions report attached.",
                attachment_path=processed_file_path
            )
            print(f"Successfully processed and sent email to {email}")
        except Exception as e:
            print(f'An error occurred in the background task: {e}')
        finally:
            # Clean up uploaded files
            if os.path.exists(input_filepath):
                os.remove(input_filepath)
            if os.path.exists(output_filepath):
                os.remove(output_filepath)

@app.route('/')
def index():
    with open('categories.json', 'r', encoding="utf-8") as f:
        categories = json.load(f)
    return render_template('index.html', categories=categories)

@app.route('/categories', methods=['POST'])
def update_categories():
    new_categories = request.get_json()
    with open('categories.json', 'w', encoding="utf-8") as f:
        json.dump(new_categories, f, indent=4)
    return 'Categories updated successfully', 200

@app.route('/rules', methods=['GET'])
def get_rules():
    with open('category_rules.json', 'r', encoding="utf-8") as f:
        rules = json.load(f)
    return jsonify(rules)

@app.route('/rules', methods=['POST'])
def update_rules():
    new_rules = request.get_json()
    with open('category_rules.json', 'w', encoding="utf-8") as f:
        json.dump(new_rules, f, indent=4)
    return 'Rules updated successfully', 200

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        flash('No file part', 'error')
        return redirect(url_for('index'))

    file = request.files['file']
    email = request.form.get('email')

    if file.filename == '':
        flash('No selected file', 'error')
        return redirect(url_for('index'))

    if not email:
        flash('Email is required', 'error')
        return redirect(url_for('index'))

    if file and file.filename.lower().endswith('.csv'):
        filename = secure_filename(file.filename)
        input_filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        output_filename = "categorized_" + filename
        output_filepath = os.path.join(app.config['UPLOAD_FOLDER'], output_filename)
        file.save(input_filepath)

        # Run the processing in a background thread
        thread = threading.Thread(target=process_and_email_task, args=(app, input_filepath, output_filepath, email))
        thread.start()

        flash('File successfully uploaded and is being processed. You will receive an email shortly.', 'success')
        return redirect(url_for('index'))
    else:
        flash('Invalid file type. Please upload a CSV file.', 'error')
        return redirect(url_for('index'))

def send_email(recipient_email, subject, body, attachment_path=None):
    if not all([SMTP_SERVER, SMTP_PORT, EMAIL_ADDRESS, EMAIL_PASSWORD]):
        raise ValueError("Email configuration is incomplete.")

    msg = MIMEMultipart()
    msg['From'] = str(Header(EMAIL_ADDRESS, 'utf-8'))
    msg['To'] = str(Header(recipient_email, 'utf-8'))
    msg['Subject'] = str(Header(subject, 'utf-8'))

    # Clean non-breaking spaces just in case
    body = body.replace('\xa0', ' ')
    msg.attach(MIMEText(body, _charset='utf-8'))

    if attachment_path and os.path.exists(attachment_path):
        try:
            with open(attachment_path, "rb") as attachment:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(attachment.read())
            encoders.encode_base64(part)
            part.add_header(
                'Content-Disposition',
                'attachment',
                # Don't use Header().encode() – just a plain string is fine
                filename='results.csv'
            )
            msg.attach(part)
        except Exception as e:
            print(f"Error attaching file {attachment_path}: {e}")

    try:
        email_content = msg.as_bytes()
        print("DEBUG: Email is ready to send")

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(msg['From'], msg['To'], email_content)

    except Exception as e:
        import traceback
        print("DEBUG: Email send error traceback:")
        traceback.print_exc()
        raise

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
