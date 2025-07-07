from openai import OpenAI
import os
import pandas as pd
import re
import time
import json

# === CONFIG ===
def load_categories():
    with open("categories.json", "r") as f:
        return json.load(f)

def get_category_tuples():
    categories_data = load_categories()
    return [(category['name'], category['budget']) for category in categories_data]

categories = get_category_tuples()
with open("category_rules.json", "r") as f:
    CATEGORY_RULES = json.load(f)

uncategorized = []
# === CLEANING ===
def clean_description(desc):
    if pd.isnull(desc):
        return ""
    desc = re.sub(r'X{4,}', '', desc)
    desc = re.sub(r'null', '', desc, flags=re.IGNORECASE)
    desc = re.sub(r'\d+', '', desc)  # remove numbers
    desc = re.sub(r'\s+', ' ', desc).strip()
    return desc

def clean_citi_csv(input_file, output_file="cleaned_transactions.csv"):
    df = pd.read_csv(input_file)
    df = df[pd.to_numeric(df['Debit'], errors='coerce').notnull()]  # keep only debits
    df['Description'] = df['Description'].apply(clean_description)
    df['Amount'] = df['Debit'].astype(float)
    cleaned_df = df[['Description', 'Amount']].copy()
    cleaned_df.to_csv(output_file, index=False)
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
def categorize_fallback(description, categories):
    for keyword, category in CATEGORY_RULES.items():
        if keyword in description.upper():
            return category
    # Fallback to GPT
    fallback_prompt = f"""Which category from this list best fits the transaction.
    There should only be one transaction categorized into the 'Rent' Category.
    If you think the transaction does not fit any of the categories, put it in 'Uncategorized'.
    : '{description}'?\nCategories: {[c[0] for c in categories]}"""
    gpt_response = query_chatgpt(fallback_prompt)

    # Basic cleanup and category match
    cleaned = gpt_response.strip().split('\n')[0]
    for name, _ in categories:
        if name.lower() in cleaned.lower():
            return name
    return "Uncategorized"

def assign_categories_to_df(filepath):
    df = pd.read_csv(filepath)
    assigned_categories = []
    categories = get_category_tuples()

    # Load a local copy of the rules to update
    with open("category_rules.json", "r") as f:
        updated_rules = json.load(f)

    for i, row in df.iterrows():
        desc = row["Description"]
        print(f"\U0001f5f2\ufe0f Categorizing [{i+1}/{len(df)}]: {desc}")
        # categorize_fallback will use the global CATEGORY_RULES, which is not modified during the loop
        category = categorize_fallback(desc, categories)

        if category == "Uncategorized":
            print(f"\U0001f575\ufe0f Found uncategorized: {desc}. Adding to rules for future runs.")
            # Use upper() to be consistent with how keywords are checked.
            updated_rules[desc.upper()] = "NEEDS CATEGORY"

        assigned_categories.append(category)
        time.sleep(1)  # prevent hammering GPT API

    df["Category"] = assigned_categories

    # Save the updated rules back to the file
    with open("category_rules.json", "w") as f:
        json.dump(updated_rules, f, indent=4)

    return df

def process_transactions(input_filepath, output_filepath):
    cleaned_df = clean_citi_csv(input_filepath)
    categorized_df = assign_categories_to_df(cleaned_df.to_csv(index=False)) # Pass the cleaned DataFrame as a string
    categorized_df.to_csv(output_filepath, index=False)
    return output_filepath
