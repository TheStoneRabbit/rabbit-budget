from openai import OpenAI
import os
import pandas as pd
import re
import time
import json

# === CONFIG ===
def get_profile_path(profile):
    return os.path.join("profiles", profile)

def load_categories(profile="default"):
    profile_path = get_profile_path(profile)
    categories_path = os.path.join(profile_path, "categories.json")
    with open(categories_path, "r", encoding="utf-8") as f:
        return json.load(f)

def get_category_tuples(profile="default"):
    categories_data = load_categories(profile)
    return [(category['name'], category['budget']) for category in categories_data]

def load_category_rules(profile="default"):
    profile_path = get_profile_path(profile)
    rules_path = os.path.join(profile_path, "category_rules.json")
    with open(rules_path, "r") as f:
        return json.load(f)

uncategorized = []
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

def clean_citi_csv(input_file):
    df = pd.read_csv(input_file, encoding="utf-8")
    df = df[pd.to_numeric(df['Debit'], errors='coerce').notnull()]  # keep only debits
    df['Description'] = df['Description'].apply(clean_description)
    df['Amount'] = df['Debit'].astype(float)
    cleaned_df = df[['Description', 'Amount']].copy()
    cleaned_df = cleaned_df.reset_index(drop=True)
    return cleaned_df

# === GPT SETUP ===
conversation_log = [{"role": "system", "content": "You are ChatGPT, a helpful assistant, that will help me categorize my credit card transactions."}]
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def query_chatgpt(prompt):
    try:
        conversation_log.append({"role": "user", "content": prompt})
        chat_completion = client.chat.completions.create(
            messages=conversation_log,
            model="gpt-4-turbo",
        )
        return chat_completion.choices[0].message.content
    except Exception as e:
        print(f"GPT fallback failed: {e}")
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
            category_rules[desc.upper()] = "NEEDS CATEGORY"

        assigned_categories.append(category)
        time.sleep(1)  # prevent hammering GPT API

    df["Category"] = assigned_categories

    # Save the updated rules back to the file
    profile_path = get_profile_path(profile)
    rules_path = os.path.join(profile_path, "category_rules.json")
    with open(rules_path, "w") as f:
        json.dump(category_rules, f, indent=4)

    return df

def process_transactions(input_filepath, output_filepath, profile="default"):
    cleaned_df = clean_citi_csv(input_filepath)
    print(f"DEBUG: Cleaned DataFrame has {len(cleaned_df)} rows.")
    categorized_df = assign_categories_to_dataframe(cleaned_df, profile)
    categorized_df.to_csv(output_filepath, index=False, encoding="utf-8")
    return output_filepath
