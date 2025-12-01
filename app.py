import os
import smtplib
import io
import csv
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from email.header import Header
import pandas as pd
from flask import Flask, request, render_template, flash, redirect, url_for, jsonify
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

import threading
from urllib.parse import unquote

# Load environment variables from .env file
load_dotenv()

from transaction_processor import (
    process_transactions,
    load_categories,
)

import storage
from storage import (
    ConflictError,
    NotFoundError,
    change_profile_password,
    create_profile,
    delete_profile,
    get_profile_settings,
    set_profile_privacy,
    verify_profile_password,
)

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

storage.init_db()

def _parse_budget(value):
    try:
        return float(value), True
    except (TypeError, ValueError):
        return 0.0, False

def build_category_summary(csv_path):
    """Aggregate total spend per category for inclusion in the email body."""
    try:
        df = pd.read_csv(csv_path)
    except Exception as error:
        print(f"Failed to read categorized CSV for summary: {error}")
        return None

    if not {'Category', 'Amount'}.issubset(df.columns):
        print("Categorized CSV missing required columns for summary.")
        return None

    try:
        amounts = pd.to_numeric(df['Amount'], errors='coerce')
        summary = (
            df.assign(Amount=amounts)
            .dropna(subset=['Amount'])
            .groupby('Category')['Amount']
            .sum()
            .sort_values(ascending=False)
        )
    except Exception as error:
        print(f"Failed to build category summary: {error}")
        return None

    if summary.empty:
        return None

    lines = [f"{category}: ${total:,.2f}" for category, total in summary.items()]
    return "\n".join(lines)

def process_and_email_task(app, input_filepath, output_filepath, email, profile):
    with app.app_context():
        try:
            # Process the transactions
            processed_file_path = process_transactions(input_filepath, output_filepath, profile)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            attachment_name = f"{profile}_{timestamp}.csv"

            summary_text = build_category_summary(processed_file_path)
            body_lines = ["Please find your categorized transactions report attached."]
            if summary_text:
                body_lines.append("")
                body_lines.append("Category spend summary:")
                body_lines.append(summary_text)
            body = "\n".join(body_lines)

            # Send email with the processed file
            send_email(
                recipient_email=email,
                subject="Your Categorized Transactions Report",
                body=body,
                attachment_path=processed_file_path,
                attachment_name=attachment_name
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
    profiles = storage.list_profiles()
    # Redirect to the first profile if one exists, or handle no profiles case
    if profiles:
        return redirect(url_for('profile_view', profile=profiles[0]))
    else:
        # Optionally, handle the case with no profiles, e.g., show a setup page
        return "No profiles found. Please create a profile."

@app.route('/profiles', methods=['POST'])
def create_profile_route():
    payload = request.get_json(silent=True) or {}
    name = (payload.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Profile name is required.'}), 400

    try:
        profile = create_profile(name)
    except ConflictError as err:
        return jsonify({'error': str(err)}), 409
    except ValueError as err:
        return jsonify({'error': str(err)}), 400

    return jsonify(profile), 201

@app.route('/profiles/<profile>', methods=['DELETE'])
def delete_profile_route(profile):
    try:
        delete_profile(profile)
    except NotFoundError as err:
        return jsonify({'error': str(err)}), 404
    except ValueError as err:
        return jsonify({'error': str(err)}), 400

    return '', 204

@app.route('/profiles/<profile>/settings', methods=['GET'])
def profile_settings_route(profile):
    try:
        settings = get_profile_settings(profile)
    except NotFoundError as err:
        return jsonify({'error': str(err)}), 404
    return jsonify(settings), 200

@app.route('/profiles/<profile>/settings/privacy', methods=['POST'])
def profile_privacy_route(profile):
    payload = request.get_json(silent=True) or {}
    is_private = bool(payload.get('is_private', False))
    password = payload.get('password')
    try:
        settings = set_profile_privacy(profile, is_private, password)
    except NotFoundError as err:
        return jsonify({'error': str(err)}), 404
    except (ValueError, ConflictError) as err:
        return jsonify({'error': str(err)}), 400
    return jsonify(settings), 200

@app.route('/profiles/<profile>/settings/change-password', methods=['POST'])
def profile_change_password_route(profile):
    payload = request.get_json(silent=True) or {}
    old_password = payload.get('old_password')
    new_password = payload.get('new_password')
    try:
        change_profile_password(profile, old_password, new_password)
    except NotFoundError as err:
        return jsonify({'error': str(err)}), 404
    except ValueError as err:
        return jsonify({'error': str(err)}), 400
    return '', 204

@app.route('/profiles/<profile>/settings/verify', methods=['POST'])
def profile_verify_password_route(profile):
    payload = request.get_json(silent=True) or {}
    password = payload.get('password')
    try:
        ok = verify_profile_password(profile, password)
    except NotFoundError as err:
        return jsonify({'error': str(err)}), 404
    return jsonify({'ok': bool(ok)}), 200


@app.route('/<profile>')
def profile_view(profile):
    profiles = storage.list_profiles()
    if profile not in profiles:
        return f"Profile '{profile}' not found.", 404
    categories = load_categories(profile)
    return render_template('index.html', categories=categories, profile=profile, profiles=profiles)

@app.route('/<profile>/categories', methods=['GET', 'POST'])
def categories_collection(profile):
    if request.method == 'GET':
        try:
            categories = storage.list_categories(profile)
        except NotFoundError as err:
            return jsonify({'error': str(err)}), 404
        return jsonify(categories), 200

    payload = request.get_json(silent=True) or {}
    name = (payload.get('name') or '').strip()
    budget_raw = payload.get('budget', 0)

    if not name:
        return jsonify({'error': 'Category name is required.'}), 400

    budget, valid_budget = _parse_budget(budget_raw)
    if not valid_budget:
        return jsonify({'error': 'Category budget must be a number.'}), 400

    try:
        new_category = storage.create_category(profile, name, budget)
    except NotFoundError as err:
        return jsonify({'error': str(err)}), 404
    except ConflictError as err:
        return jsonify({'error': str(err)}), 409
    except ValueError as err:
        return jsonify({'error': str(err)}), 400

    return jsonify(new_category), 201

@app.route('/<profile>/categories/export', methods=['GET'])
def export_categories(profile):
    password = request.args.get('password', '')
    try:
        settings = get_profile_settings(profile)
    except NotFoundError as err:
        return jsonify({'error': str(err)}), 404

    if settings.get('is_private'):
        if not verify_profile_password(profile, password):
            return jsonify({'error': 'Password required or incorrect.'}), 401

    try:
        categories = storage.list_categories(profile)
    except NotFoundError as err:
        return jsonify({'error': str(err)}), 404

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Name', 'Budget'])
    for c in categories:
        writer.writerow([c['name'], c['budget']])
    output.seek(0)

    filename = f"{profile}_categories_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    return app.response_class(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=\"{filename}\"'}
    )

@app.route('/<profile>/categories/<category_name>', methods=['PATCH', 'DELETE'])
def category_item(profile, category_name):
    decoded_name = unquote(category_name)

    if request.method == 'DELETE':
        try:
            storage.delete_category(profile, decoded_name)
        except NotFoundError as err:
            return jsonify({'error': str(err)}), 404
        return '', 204

    payload = request.get_json(silent=True) or {}

    new_name = payload.get('name', decoded_name)
    budget_raw = payload.get('budget', None)

    if budget_raw is not None:
        new_budget, valid_budget = _parse_budget(budget_raw)
        if not valid_budget:
            return jsonify({'error': 'Category budget must be a number.'}), 400
    else:
        try:
            current = next(
                c for c in storage.list_categories(profile)
                if c['name'].lower() == decoded_name.lower()
            )
            new_budget = current['budget']
        except StopIteration:
            return jsonify({'error': f"Category '{decoded_name}' not found."}), 404
        except NotFoundError as err:
            return jsonify({'error': str(err)}), 404

    try:
        updated = storage.update_category(profile, decoded_name, new_name, new_budget)
    except NotFoundError as err:
        return jsonify({'error': str(err)}), 404
    except ConflictError as err:
        return jsonify({'error': str(err)}), 409
    except ValueError as err:
        return jsonify({'error': str(err)}), 400

    return jsonify(updated), 200

@app.route('/<profile>/rules', methods=['GET'])
def get_rules(profile):
    try:
        rules = storage.list_rules(profile)
    except NotFoundError as err:
        return jsonify({'error': str(err)}), 404
    return jsonify(rules)

@app.route('/<profile>/rules', methods=['POST'])
def create_rule(profile):
    payload = request.get_json(silent=True) or {}
    keyword = (payload.get('keyword') or '').strip().upper()
    category = (payload.get('category') or '').strip()

    if not keyword:
        return jsonify({'error': 'Rule keyword is required.'}), 400
    if not category:
        return jsonify({'error': 'Rule category is required.'}), 400

    try:
        rule = storage.create_rule(profile, keyword, category)
    except NotFoundError as err:
        return jsonify({'error': str(err)}), 404
    except ConflictError as err:
        return jsonify({'error': str(err)}), 409
    except ValueError as err:
        return jsonify({'error': str(err)}), 400

    return jsonify(rule), 201

@app.route('/<profile>/rules/<rule_keyword>', methods=['PATCH', 'DELETE'])
def rule_item(profile, rule_keyword):
    decoded_keyword = unquote(rule_keyword).upper()

    if request.method == 'DELETE':
        try:
            storage.delete_rule(profile, decoded_keyword)
        except NotFoundError as err:
            return jsonify({'error': str(err)}), 404
        return '', 204

    payload = request.get_json(silent=True) or {}
    new_keyword = (payload.get('keyword') or decoded_keyword).strip().upper()
    new_category = (payload.get('category') or '').strip()

    if not new_keyword:
        return jsonify({'error': 'Rule keyword cannot be empty.'}), 400
    if not new_category:
        return jsonify({'error': 'Rule category cannot be empty.'}), 400

    try:
        updated = storage.update_rule(profile, decoded_keyword, new_keyword, new_category)
    except NotFoundError as err:
        return jsonify({'error': str(err)}), 404
    except ConflictError as err:
        return jsonify({'error': str(err)}), 409
    except ValueError as err:
        return jsonify({'error': str(err)}), 400

    return jsonify(updated), 200

@app.route('/<profile>/upload', methods=['POST'])
def upload_file(profile):
    if 'file' not in request.files:
        flash('No file part', 'error')
        return redirect(url_for('profile_view', profile=profile))

    file = request.files['file']
    email = request.form.get('email')

    if file.filename == '':
        flash('No selected file', 'error')
        return redirect(url_for('profile_view', profile=profile))

    if not email:
        flash('Email is required', 'error')
        return redirect(url_for('profile_view', profile=profile))

    if file and file.filename.lower().endswith('.csv'):
        if not storage.profile_exists(profile):
            flash(f"Profile '{profile}' not found.", 'error')
            return redirect(url_for('index'))

        filename = secure_filename(file.filename)
        input_filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        output_filename = "categorized_" + filename
        output_filepath = os.path.join(app.config['UPLOAD_FOLDER'], output_filename)
        file.save(input_filepath)

        # Run the processing in a background thread
        thread = threading.Thread(target=process_and_email_task, args=(app, input_filepath, output_filepath, email, profile))
        thread.start()

        flash('File successfully uploaded and is being processed. You will receive an email shortly.', 'success')
        return redirect(url_for('profile_view', profile=profile))
    else:
        flash('Invalid file type. Please upload a CSV file.', 'error')
        return redirect(url_for('profile_view', profile=profile))

def send_email(recipient_email, subject, body, attachment_path=None, attachment_name=None):
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
                filename=attachment_name or 'results.csv'
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
    app.run(debug=True, host='0.0.0.0', port=5001)
