from openai import OpenAI
import os
import pandas as pd
import re
import time
import tkinter as tk
from tkinter import filedialog
import json

# === CONFIG ===
categories = [
    ("Eating Out", 500.00),
    ("Groceries", 500.00),
    ("Rent", 1900.00),
    ("Public Transportation", 100.00),
    ("Repairs", 0.00),
    ("Gas", 50.00),
    ("Doctor's Office", 0.00),
    ("Prescriptions", 200.00),
    ("Fun", 600.00),
    ("Going Out", 500.00),
    ("Gifts", 100.00),
    ("Uncategorized", 0.00),
    ("Discretionary", 0.00),
    ("Subscription", 100.00),
]
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


# === UTILITY FUNCTIONS ===
def open_file_dialog(is_save=False):
    root = tk.Tk()
    root.withdraw()  # Hide the main window
    if is_save:
        file_path = filedialog.asksaveasfilename(
            title="Save processed file",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
    else:
            # Open file dialog to select a file
        file_path = filedialog.askopenfilename(
            title="Select a file to process",
            filetypes=[("All files", "*.*"), ("CSV files", "*.csv")]
        )
    return file_path


# === CATEGORIZATION ===
def categorize_fallback(description):
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

    # Load a local copy of the rules to update
    with open("category_rules.json", "r") as f:
        updated_rules = json.load(f)

    for i, row in df.iterrows():
        desc = row["Description"]
        print(f"🗂️ Categorizing [{i+1}/{len(df)}]: {desc}")
        # categorize_fallback will use the global CATEGORY_RULES, which is not modified during the loop
        category = categorize_fallback(desc)

        if category == "Uncategorized":
            print(f"🕵️ Found uncategorized: {desc}. Adding to rules for future runs.")
            # Use upper() to be consistent with how keywords are checked.
            updated_rules[desc.upper()] = "NEEDS CATEGORY"

        assigned_categories.append(category)
        time.sleep(1)  # prevent hammering GPT API

    df["Category"] = assigned_categories

    # Save the updated rules back to the file
    with open("category_rules.json", "w") as f:
        json.dump(updated_rules, f, indent=4)

    return df


# === MAIN ===
if __name__ == "__main__":
    input_path = open_file_dialog()
    cleaned_path = "cleaned_transactions.csv"
    output_path = open_file_dialog(is_save=True)


    clean_citi_csv(input_path, cleaned_path)
    print("✨ Cleaned transactions!")
    categorized_df = assign_categories_to_df(cleaned_path)
    categorized_df.to_csv(output_path, index=False)

    print("✅ Categorized transactions:")
    print("📚 Find the output in:", output_path)
