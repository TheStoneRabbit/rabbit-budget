import os
import re
import time

import pandas as pd
from openai import OpenAI

from storage import NotFoundError, list_categories, list_rules, profile_exists, upsert_rule, get_profile_settings
# === CLEANING ===
def clean_description(desc):
    if pd.isnull(desc):
        return ""
    desc = re.sub(r'X{4,}', '', desc)
    desc = re.sub(r'null', '', desc, flags=re.IGNORECASE)
    desc = re.sub(r'\d+', '', desc)  # remove numbers
    desc = re.sub(r'\s+', ' ', desc).strip()
    desc = desc.replace('\xa0', ' ') # Replace non-breaking space with regular space
    return desc

def clean_citi_csv(input_file, profile="default"):
    settings = get_profile_settings(profile)
    use_custom = settings.get("custom_columns")
    description_col = settings.get("description_column") or "Description"
    amount_col = settings.get("amount_column") or "Debit"

    df = pd.read_csv(input_file, encoding="utf-8")
    if use_custom:
        if description_col not in df.columns or amount_col not in df.columns:
            raise ValueError(f"CSV missing required columns: '{description_col}' and/or '{amount_col}'")
    else:
        if "Description" not in df.columns or "Debit" not in df.columns:
            raise ValueError("CSV missing required columns: 'Description' and/or 'Debit'")

    amounts = pd.to_numeric(df[amount_col], errors='coerce')
    has_negatives = (amounts < 0).any()
    if has_negatives:
        df = df[amounts < 0]
    else:
        df = df[amounts.notnull()]
    df['Description'] = df[description_col].apply(clean_description)
    df['Amount'] = df[amount_col].astype(float).abs()
    cleaned_df = df[['Description', 'Amount']].copy()
    cleaned_df = cleaned_df.reset_index(drop=True)
    return cleaned_df

# === GPT SETUP ===
SYSTEM_PROMPT = "You are ChatGPT, a helpful assistant, that will help me categorize my credit card transactions."
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def query_chatgpt(prompt):
    try:
        conversation = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        chat_completion = client.responses.create(
            model="gpt-4.1-mini",
            input=conversation,
        )
        return chat_completion.output_text
    except Exception as error:
        print(f"GPT fallback failed: {error}")
        return "Uncategorized"


# === CATEGORIZATION ===
def categorize_fallback(description, categories, category_rules):
    for keyword, category in category_rules.items():
        if keyword in description.upper():
            return category
    # Fallback to GPT
    fallback_prompt = f"""Which category from this list best fits the transaction.
    There should only be one transaction categorized into the 'Rent' Category.
    If you think the transaction does not fit any of the categories, put it in 'Uncategorized'.
    : '{description}'?
Categories: {[c[0] for c in categories]}"""
    gpt_response = query_chatgpt(fallback_prompt)

    # Basic cleanup and category match
    cleaned = gpt_response.strip().split('\n')[0]
    for name, _ in categories:
        if name.lower() in cleaned.lower():
            return name
    return "Uncategorized"

def assign_categories_to_dataframe(df, profile="default"):
    assigned_categories = []
    categories = get_category_tuples(profile)
    category_rules = load_category_rules(profile)

    for i in df.index:
        row = df.loc[i]
        desc = row["Description"]
        print(f"🗂️ Categorizing [{i+1}/{len(df)}]: {desc}")
        category = categorize_fallback(desc, categories, category_rules)

        if category == "Uncategorized":
            print(f"\U0001f575\ufe0f Found uncategorized: {desc}. Adding to rules for future runs.")
            keyword = desc.upper()
            category_rules[keyword] = "NEEDS CATEGORY"
            try:
                upsert_rule(profile, keyword, "NEEDS CATEGORY")
            except NotFoundError:
                print(f"Profile '{profile}' not found while updating rules.")

        assigned_categories.append(category)
        time.sleep(1)  # prevent hammering GPT API

    df["Category"] = assigned_categories

    return df

# === STORAGE HELPERS ===
def load_categories(profile="default"):
    try:
        return list_categories(profile)
    except NotFoundError:
        return []

def get_category_tuples(profile="default"):
    categories_data = load_categories(profile)
    return [(category['name'], category['budget']) for category in categories_data]

def load_category_rules(profile="default"):
    try:
        return list_rules(profile)
    except NotFoundError:
        return {}

def process_transactions(input_filepath, output_filepath, profile="default"):
    if not profile_exists(profile):
        raise NotFoundError(f"Profile '{profile}' not found.")
    cleaned_df = clean_citi_csv(input_filepath, profile=profile)
    print(f"DEBUG: Cleaned DataFrame has {len(cleaned_df)} rows.")
    categorized_df = assign_categories_to_dataframe(cleaned_df, profile)
    categorized_df.to_csv(output_filepath, index=False, encoding="utf-8")
    return output_filepath
